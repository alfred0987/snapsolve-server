"""
SnapSolve Backend Server
Handles: auth, credits, Claude API, admin panel, Venmo manual payments, NOWPayments crypto

Run: python server.py
Then open http://localhost:5000/admin to manage users

Install:
    pip install flask anthropic supabase bcrypt pyjwt requests flask-cors
"""

from flask import Flask, request, jsonify, render_template_string, redirect, session
from flask_cors import CORS
import anthropic
import bcrypt
import jwt
import requests
import base64
import io
import hashlib
import hmac
import os
import secrets
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta
from supabase import create_client

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET", "change-this-to-something-random")
CORS(app)

# ══════════════════════════════════════════════════════════════════════════════
#  CONFIG — set these as environment variables in Render dashboard
# ══════════════════════════════════════════════════════════════════════════════
SUPABASE_URL           = os.environ.get("SUPABASE_URL", "YOUR_SUPABASE_URL")
SUPABASE_KEY           = os.environ.get("SUPABASE_KEY", "YOUR_SUPABASE_KEY")
ANTHROPIC_API_KEY      = os.environ.get("ANTHROPIC_API_KEY", "YOUR_ANTHROPIC_KEY")
NOWPAYMENTS_API_KEY    = os.environ.get("NOWPAYMENTS_API_KEY", "")
NOWPAYMENTS_IPN_SECRET = os.environ.get("NOWPAYMENTS_IPN_SECRET", "")
JWT_SECRET             = os.environ.get("JWT_SECRET", "change-this")
ADMIN_PASSWORD         = os.environ.get("ADMIN_PASSWORD", "change-this")
VENMO_HANDLE           = os.environ.get("VENMO_HANDLE", "@your-venmo-handle")
GMAIL_USER             = os.environ.get("GMAIL_USER", "thesnaptutor@gmail.com")
GMAIL_APP_PASSWORD     = os.environ.get("GMAIL_APP_PASSWORD", "")
YOUR_DOMAIN            = os.environ.get("YOUR_DOMAIN", "http://localhost:5000")

# Credit packs
CREDIT_PACKS = {
    "basic":   {"credits": 100,  "price": 5.00,  "label": "Basic — 100 credits"},
    "standard":{"credits": 250,  "price": 10.00, "label": "Standard — 250 credits"},
    "pro":     {"credits": 700,  "price": 25.00, "label": "Pro — 700 credits"},
}
# ══════════════════════════════════════════════════════════════════════════════

db = create_client(SUPABASE_URL, SUPABASE_KEY)
ai = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)


# ── Helpers ────────────────────────────────────────────────────────────────────

def make_token(user_id):
    payload = {"user_id": user_id, "exp": datetime.utcnow() + timedelta(days=30)}
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")

def verify_token(req):
    auth = req.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None
    try:
        payload = jwt.decode(auth.split(" ", 1)[1], JWT_SECRET, algorithms=["HS256"])
        return payload["user_id"]
    except:
        return None

def get_user(user_id):
    res = db.table("users").select("*").eq("id", user_id).single().execute()
    return res.data

def err(msg, code=400):
    return jsonify({"error": msg}), code

def ok(data):
    return jsonify(data), 200


# ── Auth ───────────────────────────────────────────────────────────────────────

@app.route("/register", methods=["POST"])
def register():
    body     = request.json or {}
    email    = (body.get("email") or "").strip().lower()
    password = body.get("password") or ""

    if not email or not password:
        return err("Email and password required")
    if len(password) < 6:
        return err("Password must be at least 6 characters")

    existing = db.table("users").select("id").eq("email", email).execute()
    if existing.data:
        return err("Email already registered")

    hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    res    = db.table("users").insert({
        "email": email, "password_hash": hashed, "credits": 5,
        "tos_agreed": datetime.utcnow().isoformat()
    }).execute()

    user  = res.data[0]
    token = make_token(user["id"])
    print(f"✅ Registered: {email} (+5 free credits)")
    return ok({"token": token, "credits": 5, "email": email})


@app.route("/login", methods=["POST"])
def login():
    body     = request.json or {}
    email    = (body.get("email") or "").strip().lower()
    password = body.get("password") or ""

    if not email or not password:
        return err("Email and password required")

    res = db.table("users").select("*").eq("email", email).execute()
    if not res.data:
        return err("Invalid email or password")

    user = res.data[0]
    if not bcrypt.checkpw(password.encode(), user["password_hash"].encode()):
        return err("Invalid email or password")

    token = make_token(user["id"])
    print(f"✅ Login: {email} ({user['credits']} credits)")
    return ok({"token": token, "credits": user["credits"], "email": email})


@app.route("/question-count", methods=["GET"])
def question_count():
    try:
        res = db.table("usage_log").select("id", count="exact").execute()
        return ok({"count": res.count or 0})
    except:
        return ok({"count": 0})
    user_id = verify_token(request)
    if not user_id:
        return err("Unauthorized", 401)
    user = get_user(user_id)
    return ok({"credits": user["credits"], "email": user["email"]})


# ── Solve ──────────────────────────────────────────────────────────────────────

@app.route("/solve", methods=["POST"])
def solve():
    user_id = verify_token(request)
    if not user_id:
        return err("Unauthorized", 401)

    user = get_user(user_id)
    if user["credits"] < 1:
        return err("No credits remaining. Visit the website to buy more.", 402)

    body    = request.json or {}
    img_b64 = body.get("image")
    if not img_b64:
        return err("No image provided")

    try:
        prompt = """You are a fast, accurate academic quiz solver.

Look at this screenshot of a question. Determine the type and answer it.

IF MULTIPLE CHOICE (any labeled answer options exist — A/B/C/D, bubbles, buttons, numbered choices):
TYPE: multiple_choice
ANSWER: B
EXPLANATION: One sentence why.
CONFIDENCE: 95

IF OPEN ENDED (student must type a free response with no provided options):
TYPE: open_ended
ANSWER: the direct answer
EXPLANATION: One short sentence of context.
CONFIDENCE: 90

Rules:
- Start with TYPE: immediately, nothing before it
- ANSWER for multiple choice = exactly A, B, C, or D only — never the answer text
- For 2x2 grid layouts: top-left=A, top-right=B, bottom-left=C, bottom-right=D"""

        response = ai.messages.create(
            model="claude-opus-4-5",
            max_tokens=300,
            messages=[{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": img_b64}},
                {"type": "text",  "text": prompt}
            ]}]
        )

        raw         = response.content[0].text.strip()
        new_credits = user["credits"] - 1
        db.table("users").update({"credits": new_credits}).eq("id", user_id).execute()
        db.table("usage_log").insert({"user_id": user_id}).execute()

        print(f"⚡ Solve: {user['email']} | {new_credits} credits left")
        return ok({"raw": raw, "credits_remaining": new_credits})

    except Exception as e:
        return err(f"AI error: {str(e)}")


# ── Referral system ────────────────────────────────────────────────────────────

@app.route("/apply-referral", methods=["POST"])
def apply_referral():
    user_id = verify_token(request)
    if not user_id:
        return err("Unauthorized", 401)

    body = request.json or {}
    code = (body.get("code") or "").strip().upper()

    if not code:
        return err("No code provided")

    # Check if user already used a referral code
    existing = db.table("referral_links").select("id").eq("user_id", user_id).execute()
    if existing.data:
        return err("You have already used a referral code")

    # Find the referral code
    code_res = db.table("referral_codes").select("*").eq("code", code).execute()
    if not code_res.data:
        return err("Invalid referral code")

    referral = code_res.data[0]

    # Can't use your own code
    if referral["owner_user_id"] == user_id:
        return err("You can't use your own referral code")

    # Link user to referral code
    db.table("referral_links").insert({
        "user_id": user_id,
        "referral_code_id": referral["id"]
    }).execute()

    # Give user 15 free credits
    user = get_user(user_id)
    db.table("users").update({"credits": user["credits"] + 15}).eq("id", user_id).execute()

    print(f"✅ Referral: user {user_id} used code {code} — +15 credits")
    return ok({"message": "Referral code applied! 15 credits added to your account.", "credits": user["credits"] + 15})


@app.route("/create-referral-code", methods=["POST"])
def create_referral_code():
    user_id = verify_token(request)
    if not user_id:
        return err("Unauthorized", 401)

    body = request.json or {}
    code = (body.get("code") or "").strip().upper()

    if not code or len(code) < 3 or len(code) > 20:
        return err("Code must be 3-20 characters")

    # Check if code already exists
    existing = db.table("referral_codes").select("id").eq("code", code).execute()
    if existing.data:
        return err("That code is already taken")

    user = get_user(user_id)
    db.table("referral_codes").insert({
        "code": code,
        "owner_email": user["email"],
        "owner_user_id": user_id
    }).execute()

    return ok({"message": f"Referral code {code} created!", "code": code})


@app.route("/my-referral-stats", methods=["GET"])
def my_referral_stats():
    user_id = verify_token(request)
    if not user_id:
        return err("Unauthorized", 401)

    # Get their referral code
    code_res = db.table("referral_codes").select("*").eq("owner_user_id", user_id).execute()
    if not code_res.data:
        return ok({"code": None, "total_referred": 0, "total_earned": 0, "earnings": []})

    referral = code_res.data[0]

    # Get earnings
    earnings = db.table("affiliate_earnings").select("*").eq("affiliate_user_id", user_id).order("created_at", desc=True).execute()
    total_earned = sum(e["amount"] for e in earnings.data)

    # Count referred users
    links = db.table("referral_links").select("*").eq("referral_code_id", referral["id"]).execute()

    return ok({
        "code": referral["code"],
        "total_referred": len(links.data),
        "total_earned": round(total_earned, 2),
        "earnings": earnings.data[:20]
    })


def pay_affiliate(user_id, purchase_id, amount_paid):
    """Called after every purchase to pay affiliate their 10% cut."""
    try:
        # Check if this user was referred
        link = db.table("referral_links").select("*").eq("user_id", user_id).execute()
        if not link.data:
            return

        referral_code_id = link.data[0]["referral_code_id"]

        # Get the affiliate
        code = db.table("referral_codes").select("*").eq("id", referral_code_id).execute()
        if not code.data:
            return

        affiliate_user_id = code.data[0]["owner_user_id"]
        if affiliate_user_id == user_id:
            return  # shouldn't happen but just in case

        affiliate_cut = round(amount_paid * 0.10, 2)

        db.table("affiliate_earnings").insert({
            "affiliate_user_id": affiliate_user_id,
            "referred_user_id":  user_id,
            "purchase_id":       purchase_id,
            "amount":            affiliate_cut
        }).execute()

        print(f"💸 Affiliate cut: {affiliate_cut} to {affiliate_user_id} for purchase {purchase_id}")
    except Exception as e:
        print(f"⚠️ Affiliate payment error: {e}")


# ── Email helper ───────────────────────────────────────────────────────────────

def send_email(to, subject, body_html):
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = GMAIL_USER
        msg["To"]      = to
        msg.attach(MIMEText(body_html, "html"))
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(GMAIL_USER, GMAIL_APP_PASSWORD)
            server.sendmail(GMAIL_USER, to, msg.as_string())
        return True
    except Exception as e:
        print(f"⚠️ Email error: {e}")
        return False


# ── Password reset ─────────────────────────────────────────────────────────────

@app.route("/forgot-password", methods=["POST"])
def forgot_password():
    body  = request.json or {}
    email = (body.get("email") or "").strip().lower()

    if not email:
        return err("Email required")

    res = db.table("users").select("id").eq("email", email).execute()
    if not res.data:
        # Don't reveal if email exists or not
        return ok({"message": "If that email exists you'll receive a reset link shortly."})

    user_id = res.data[0]["id"]
    token   = secrets.token_urlsafe(32)
    expiry  = (datetime.utcnow() + timedelta(hours=1)).isoformat()

    # Store reset token
    db.table("password_resets").upsert({
        "user_id": user_id,
        "token":   token,
        "expires_at": expiry
    }).execute()

    reset_url = f"https://thesnaptutor.com/reset.html?token={token}"

    html = f"""
    <div style="font-family: 'Segoe UI', sans-serif; max-width: 480px; margin: 0 auto; background: #0f172a; color: #e2e8f0; padding: 40px; border-radius: 12px;">
      <div style="font-size: 28px; font-weight: 800; color: #e8ff47; margin-bottom: 8px;">⚡ Snap Tutor</div>
      <div style="font-size: 18px; font-weight: 600; margin-bottom: 16px;">Reset your password</div>
      <p style="color: #94a3b8; font-size: 14px; line-height: 1.7; margin-bottom: 28px;">
        We received a request to reset your password. Click the button below to set a new one. This link expires in 1 hour.
      </p>
      <a href="{reset_url}" style="display: inline-block; background: #e8ff47; color: #000; padding: 14px 32px; font-weight: 700; font-size: 15px; text-decoration: none; border-radius: 8px; margin-bottom: 24px;">
        Reset Password
      </a>
      <p style="color: #475569; font-size: 12px;">If you didn't request this, ignore this email. Your password won't change.</p>
    </div>
    """

    send_email(email, "Reset your Snap Tutor password", html)
    return ok({"message": "If that email exists you'll receive a reset link shortly."})


@app.route("/reset-password", methods=["POST"])
def reset_password():
    body     = request.json or {}
    token    = body.get("token") or ""
    password = body.get("password") or ""

    if not token or not password:
        return err("Token and password required")
    if len(password) < 6:
        return err("Password must be at least 6 characters")

    res = db.table("password_resets").select("*").eq("token", token).execute()
    if not res.data:
        return err("Invalid or expired reset link")

    reset = res.data[0]

    # Check expiry
    expiry = datetime.fromisoformat(reset["expires_at"])
    if datetime.utcnow() > expiry:
        return err("Reset link has expired. Please request a new one.")

    hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    db.table("users").update({"password_hash": hashed}).eq("id", reset["user_id"]).execute()
    db.table("password_resets").delete().eq("token", token).execute()

    print(f"✅ Password reset for user {reset['user_id']}")
    return ok({"message": "Password reset successfully! You can now log in."})


# ── Reviews ────────────────────────────────────────────────────────────────────

@app.route("/submit-review", methods=["POST"])
def submit_review():
    user_id = verify_token(request)
    if not user_id:
        return err("Unauthorized", 401)

    body         = request.json or {}
    review       = (body.get("review") or "").strip()
    rating       = int(body.get("rating") or 5)
    display_name = (body.get("display_name") or "").strip()

    if not review:
        return err("Review cannot be empty")
    if len(review) > 500:
        return err("Review must be under 500 characters")

    user = get_user(user_id)

    db.table("reviews").insert({
        "user_id":      user_id,
        "email":        user["email"],
        "review":       review,
        "rating":       rating,
        "display_name": display_name if display_name else None,
        "approved":     False
    }).execute()

    return ok({"message": "Thanks for your feedback! Your review will appear after approval."})


@app.route("/reviews", methods=["GET"])
def get_reviews():
    res = db.table("reviews").select("*").eq("approved", True).order("created_at", desc=True).execute()
    return ok({"reviews": res.data})


# ── NOWPayments crypto ─────────────────────────────────────────────────────────

@app.route("/create-crypto-payment-guest", methods=["POST"])
def create_crypto_payment_guest():
    body  = request.json or {}
    email = (body.get("email") or "").strip().lower()
    pack  = body.get("pack")

    if not email:
        return err("Email required")
    if pack not in CREDIT_PACKS:
        return err("Invalid pack")

    # Check user exists
    res = db.table("users").select("id").eq("email", email).execute()
    if not res.data:
        return err("No account found with that email. Please register in the app first.")

    user_id = res.data[0]["id"]
    p       = CREDIT_PACKS[pack]

    payload = {
        "price_amount":     p["price"],
        "price_currency":   "usd",
        "pay_currency":     "usdterc20",  # USDT on Ethereum — no minimum issues
        "order_id":         f"{user_id}:{pack}",
        "order_description": f"SnapTutor {p['label']}",
        "ipn_callback_url": YOUR_DOMAIN + "/nowpayments-webhook",
        "success_url":      YOUR_DOMAIN + "/?payment=success",
        "cancel_url":       YOUR_DOMAIN + "/?payment=cancel",
    }

    res2 = requests.post(
        "https://api.nowpayments.io/v1/invoice",
        json=payload,
        headers={"x-api-key": NOWPAYMENTS_API_KEY}
    )

    if res2.status_code != 200:
        return err("Failed to create payment: " + res2.text)

    data = res2.json()
    return ok({"url": data["invoice_url"]})


@app.route("/create-crypto-payment", methods=["POST"])
def create_crypto_payment():
    user_id = verify_token(request)
    if not user_id:
        return err("Unauthorized", 401)

    body = request.json or {}
    pack = body.get("pack")
    if pack not in CREDIT_PACKS:
        return err("Invalid pack")

    p    = CREDIT_PACKS[pack]
    user = get_user(user_id)

    payload = {
        "price_amount":    p["price"],
        "price_currency":  "usd",
        "pay_currency":    "usdttrc20",  # USDT by default, user can change on NOWPayments page
        "order_id":        f"{user_id}:{pack}",
        "order_description": f"SnapSolve {p['label']}",
        "ipn_callback_url": YOUR_DOMAIN + "/nowpayments-webhook",
        "success_url":     YOUR_DOMAIN + "/success.html",
        "cancel_url":      YOUR_DOMAIN + "/cancel.html",
    }

    res = requests.post(
        "https://api.nowpayments.io/v1/invoice",
        json=payload,
        headers={"x-api-key": NOWPAYMENTS_API_KEY}
    )

    if res.status_code != 200:
        return err("Failed to create payment: " + res.text)

    data = res.json()
    return ok({"url": data["invoice_url"]})


@app.route("/nowpayments-webhook", methods=["POST"])
def nowpayments_webhook():
    # Verify signature
    sig       = request.headers.get("x-nowpayments-sig", "")
    body      = request.data
    expected  = hmac.new(
        NOWPAYMENTS_IPN_SECRET.encode(),
        body,
        hashlib.sha512
    ).hexdigest()

    if not hmac.compare_digest(sig, expected):
        return err("Invalid signature", 400)

    data   = request.json
    status = data.get("payment_status")

    if status in ("finished", "confirmed"):
        order_id = data.get("order_id", "")
        try:
            user_id, pack = order_id.split(":", 1)
        except:
            return err("Bad order_id")

        if pack not in CREDIT_PACKS:
            return err("Bad pack")

        credits = CREDIT_PACKS[pack]["credits"]
        amount  = CREDIT_PACKS[pack]["price"]
        user    = get_user(user_id)

        new_credits = user["credits"] + credits
        db.table("users").update({"credits": new_credits}).eq("id", user_id).execute()
        purchase = db.table("purchases").insert({
            "user_id":       user_id,
            "credits_added": credits,
            "amount_paid":   amount,
            "stripe_session_id": data.get("payment_id", "crypto")
        }).execute()

        # Pay affiliate if applicable
        if purchase.data:
            pay_affiliate(user_id, purchase.data[0]["id"], amount)

        print(f"💰 Crypto payment: user {user_id} +{credits} credits ({pack})")

    return ok({"received": True})


# ── Admin panel ────────────────────────────────────────────────────────────────

ADMIN_HTML = """
<!DOCTYPE html>
<html>
<head>
<title>SnapTutor Admin</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: 'Segoe UI', sans-serif; background: #0f172a; color: #e2e8f0; min-height: 100vh; padding: 32px; }
  h1 { color: #e8ff47; font-size: 28px; margin-bottom: 8px; }
  .sub { color: #64748b; margin-bottom: 32px; font-size: 14px; }
  .card { background: #1e293b; border-radius: 12px; padding: 24px; margin-bottom: 24px; }
  .card h2 { font-size: 16px; color: #94a3b8; margin-bottom: 16px; text-transform: uppercase; letter-spacing: 0.1em; }
  input, select { background: #0f172a; border: 1px solid #334155; border-radius: 8px; padding: 10px 14px; color: #e2e8f0; font-size: 14px; width: 100%; margin-bottom: 10px; }
  button { background: #e8ff47; color: #000; border: none; border-radius: 8px; padding: 10px 20px; font-weight: 700; cursor: pointer; font-size: 14px; }
  button:hover { background: #d4eb33; }
  .btn-red { background: #dc2626; color: #fff; }
  .btn-red:hover { background: #b91c1c; }
  .btn-sm { padding: 6px 12px; font-size: 12px; }
  .msg { padding: 12px; border-radius: 8px; margin-bottom: 16px; font-size: 14px; }
  .msg.ok  { background: #14532d; color: #86efac; }
  .msg.err { background: #7f1d1d; color: #fca5a5; }
  table { width: 100%; border-collapse: collapse; font-size: 13px; }
  th { text-align: left; color: #64748b; padding: 8px 12px; border-bottom: 1px solid #334155; }
  td { padding: 10px 12px; border-bottom: 1px solid #1a2535; vertical-align: middle; }
  tr:hover td { background: #162030; }
  .badge { background: #2563eb; color: #fff; border-radius: 4px; padding: 2px 8px; font-size: 12px; font-weight: 700; }
  .badge-green { background: #16a34a; }
  .badge-yellow { background: #d97706; }
  .row { display: flex; gap: 12px; }
  .row input { flex: 1; }
  .venmo { background: #1e3a5f; border-radius: 8px; padding: 16px; margin-bottom: 16px; }
  .venmo strong { color: #60a5fa; }
  .bank-card { background: #0f1a2a; border: 1px solid #1e2a3a; border-radius: 8px; padding: 16px 20px; margin-bottom: 12px; display:flex; align-items:center; justify-content:space-between; gap:16px; flex-wrap:wrap; }
  .bank-email { font-weight: 600; font-size: 14px; }
  .bank-code { font-size: 12px; color: #64748b; margin-top: 2px; font-family: monospace; }
  .bank-owed { font-size: 24px; font-weight: 700; color: #e8ff47; }
  .bank-owed.zero { color: #334155; }
  .bank-meta { font-size: 12px; color: #64748b; margin-top: 2px; }
  .section-sep { border-top: 1px solid #1e2a3a; margin: 8px 0 16px; }
</style>
</head>
<body>
<h1>⚡ SnapTutor Admin</h1>
<p class="sub">Manage users, credits, affiliates, and payments</p>

{% if msg %}
<div class="msg {{ msg_type }}">{{ msg }}</div>
{% endif %}

<!-- Manual Payments -->
<div class="card">
  <h2>📱 Add Credits (Venmo Payment)</h2>
  <div class="venmo">
    Your Venmo: <strong>{{ venmo }}</strong><br><br>
    When someone pays, enter their email, how many credits to add, and the amount they paid.
  </div>
  <form method="POST" action="/admin/add-credits">
    <div class="row">
      <input name="email" placeholder="Customer email" required />
      <input name="credits" type="number" placeholder="Credits" required style="max-width:120px" />
      <input name="amount_paid" type="number" step="0.01" placeholder="$ Paid" required style="max-width:120px" />
    </div>
    <button type="submit">➕ Add Credits</button>
  </form>
</div>

<!-- Search user -->
<div class="card">
  <h2>🔍 Look Up User</h2>
  <form method="GET" action="/admin">
    <div class="row">
      <input name="search" placeholder="Search by email" value="{{ search or '' }}" />
      <button type="submit">Search</button>
    </div>
  </form>
  {% if users %}
  <table>
    <tr><th>Email</th><th>Credits</th><th>Joined</th><th>Action</th></tr>
    {% for u in users %}
    <tr>
      <td>{{ u.email }}</td>
      <td><span class="badge">{{ u.credits }}</span></td>
      <td>{{ u.created_at[:10] }}</td>
      <td>
        <form method="POST" action="/admin/delete-user" style="margin:0;">
          <input type="hidden" name="email" value="{{ u.email }}" />
          <button type="submit" class="btn-sm btn-red" onclick="return confirm('Delete {{ u.email }}? This cannot be undone.')">Delete</button>
        </form>
      </td>
    </tr>
    {% endfor %}
  </table>
  {% endif %}
</div>

<!-- Affiliate bank accounts -->
<div class="card">
  <h2>🏦 Affiliate Bank Accounts</h2>
  <p style="color:#64748b; font-size:13px; margin-bottom:16px;">Total owed to each affiliate. Mark as paid once you've sent them their money.</p>
  {% if affiliate_balances %}
    {% for a in affiliate_balances %}
    <div class="bank-card">
      <div>
        <div class="bank-email">{{ a.email }}</div>
        <div class="bank-code">Code: {{ a.code }}</div>
        <div class="bank-meta">{{ a.total_referrals }} referrals · {{ a.total_sales }} sales</div>
      </div>
      <div style="text-align:right;">
        <div class="bank-owed {% if a.owed == 0 %}zero{% endif %}">${{ "%.2f"|format(a.owed) }}</div>
        <div class="bank-meta">lifetime earned: ${{ "%.2f"|format(a.lifetime) }}</div>
      </div>
      {% if a.owed > 0 %}
      <form method="POST" action="/admin/pay-affiliate" style="margin:0;">
        <input type="hidden" name="affiliate_user_id" value="{{ a.user_id }}" />
        <input type="hidden" name="amount" value="{{ a.owed }}" />
        <button type="submit" class="btn-red btn-sm">✓ Mark Paid (${{ "%.2f"|format(a.owed) }})</button>
      </form>
      {% else %}
      <span style="font-size:12px; color:#334155; font-weight:600;">ALL PAID ✓</span>
      {% endif %}
    </div>
    {% endfor %}
  {% else %}
  <p style="color:#334155; font-size:13px;">No affiliates yet.</p>
  {% endif %}
</div>

<!-- Affiliate codes -->
<div class="card">
  <h2>🔗 Create Referral Code</h2>
  <form method="POST" action="/admin/create-referral-code">
    <div class="row">
      <input name="email" placeholder="Affiliate email" required />
      <input name="code" placeholder="Code (e.g. JOHN10)" required style="max-width:180px; text-transform:uppercase;" />
    </div>
    <button type="submit">➕ Create Code</button>
  </form>
</div>

<!-- Referral signups log -->
<div class="card">
  <h2>📥 Referral Signups</h2>
  <p style="color:#64748b; font-size:13px; margin-bottom:12px;">Who signed up using each affiliate code.</p>
  <table>
    <tr><th>Affiliate Code</th><th>Affiliate Email</th><th>New User Email</th><th>Date</th><th>Action</th></tr>
    {% for s in referral_signups %}
    <tr>
      <td><span class="badge badge-yellow">{{ s.code }}</span></td>
      <td>{{ s.affiliate_email }}</td>
      <td>{{ s.user_email }}</td>
      <td>{{ s.created_at[:10] }}</td>
      <td>
        <form method="POST" action="/admin/delete-referral-code" style="margin:0;">
          <input type="hidden" name="code_id" value="{{ s.code_id }}" />
          <button type="submit" class="btn-sm btn-red" onclick="return confirm('Delete this referral code?')">Delete Code</button>
        </form>
      </td>
    </tr>
    {% endfor %}
    {% if not referral_signups %}
    <tr><td colspan="5" style="color:#334155;">No signups yet.</td></tr>
    {% endif %}
  </table>
</div>

<!-- Affiliate earnings log -->
<div class="card">
  <h2>💸 Affiliate Earnings Log</h2>
  <p style="color:#64748b; font-size:13px; margin-bottom:12px;">Every purchase made by a referred user and what the affiliate earned.</p>
  <table>
    <tr><th>Affiliate Email</th><th>Customer Email</th><th>Sale Amount</th><th>Affiliate Cut</th><th>Date</th></tr>
    {% for e in affiliate_earnings %}
    <tr>
      <td>{{ e.affiliate_email }}</td>
      <td>{{ e.referred_email }}</td>
      <td>${{ "%.2f"|format(e.sale_amount) }}</td>
      <td style="color:#e8ff47; font-weight:700;">${{ "%.2f"|format(e.amount) }}</td>
      <td>{{ e.created_at[:10] }}</td>
    </tr>
    {% endfor %}
    {% if not affiliate_earnings %}
    <tr><td colspan="5" style="color:#334155;">No earnings yet.</td></tr>
    {% endif %}
  </table>
</div>

<!-- Reviews -->
<div class="card">
  <h2>⭐ Reviews</h2>
  <p style="color:#64748b; font-size:13px; margin-bottom:16px;">Approve reviews to show them publicly on the website.</p>
  <table>
    <tr><th>Email</th><th>Review</th><th>Rating</th><th>Date</th><th>Action</th></tr>
    {% for r in reviews %}
    <tr>
      <td>{{ r.email }}</td>
      <td style="max-width:300px;">{{ r.review[:100] }}{% if r.review|length > 100 %}...{% endif %}</td>
      <td>{{ r.rating }}/5</td>
      <td>{{ r.created_at[:10] }}</td>
      <td>
        {% if not r.approved %}
        <form method="POST" action="/admin/approve-review" style="margin:0; display:inline;">
          <input type="hidden" name="review_id" value="{{ r.id }}" />
          <button type="submit" class="btn-sm" style="background:#16a34a; color:#fff; border:none; border-radius:4px; padding:4px 10px; cursor:pointer; font-size:12px;">Approve</button>
        </form>
        {% else %}
        <form method="POST" action="/admin/unapprove-review" style="margin:0; display:inline;">
          <input type="hidden" name="review_id" value="{{ r.id }}" />
          <button type="submit" class="btn-sm" style="background:#d97706; color:#fff; border:none; border-radius:4px; padding:4px 10px; cursor:pointer; font-size:12px;">Unpublish</button>
        </form>
        {% endif %}
        <form method="POST" action="/admin/delete-review" style="margin:0; display:inline; margin-left:6px;">
          <input type="hidden" name="review_id" value="{{ r.id }}" />
          <button type="submit" class="btn-sm btn-red" style="font-size:12px;">Delete</button>
        </form>
      </td>
    </tr>
    {% endfor %}
    {% if not reviews %}
    <tr><td colspan="5" style="color:#334155;">No reviews yet.</td></tr>
    {% endif %}
  </table>
</div>

<!-- Recent purchases -->
<div class="card">
  <h2>💰 Recent Purchases</h2>
  <table>
    <tr><th>Email</th><th>Credits</th><th>Amount Paid</th><th>Date</th></tr>
    {% for p in purchases %}
    <tr>
      <td>{{ p.email }}</td>
      <td>+{{ p.credits_added }}</td>
      <td>${{ "%.2f"|format(p.amount_paid) }}</td>
      <td>{{ p.created_at[:10] }}</td>
    </tr>
    {% endfor %}
  </table>
</div>

</body>
</html>
"""

@app.route("/admin", methods=["GET"])
def admin():
    if not session.get("admin"):
        return redirect("/admin/login")

    search   = request.args.get("search", "")
    msg      = request.args.get("msg", "")
    msg_type = request.args.get("type", "ok")

    users = []
    if search:
        res   = db.table("users").select("*").ilike("email", f"%{search}%").execute()
        users = res.data

    # Recent purchases with emails
    purchases_raw = db.table("purchases").select("*").order("created_at", desc=True).limit(20).execute().data
    purchases = []
    for p in purchases_raw:
        try:
            u = db.table("users").select("email").eq("id", p["user_id"]).single().execute()
            p["email"] = u.data["email"] if u.data else p["user_id"][:8]
        except:
            p["email"] = p["user_id"][:8]
        purchases.append(p)

    # Affiliate earnings with emails
    earnings_raw = db.table("affiliate_earnings").select("*").order("created_at", desc=True).limit(50).execute().data
    affiliate_earnings = []
    for e in earnings_raw:
        try:
            aff = db.table("users").select("email").eq("id", e["affiliate_user_id"]).single().execute()
            ref = db.table("users").select("email").eq("id", e["referred_user_id"]).single().execute()
            pur = db.table("purchases").select("amount_paid").eq("id", e["purchase_id"]).single().execute()
            e["affiliate_email"] = aff.data["email"] if aff.data else "unknown"
            e["referred_email"]  = ref.data["email"] if ref.data else "unknown"
            e["sale_amount"]     = pur.data["amount_paid"] if pur.data else 0
        except:
            e["affiliate_email"] = "unknown"
            e["referred_email"]  = "unknown"
            e["sale_amount"]     = 0
        affiliate_earnings.append(e)

    # Referral signups log
    links_raw = db.table("referral_links").select("*").order("created_at", desc=True).limit(50).execute().data
    referral_signups = []
    for l in links_raw:
        try:
            user  = db.table("users").select("email").eq("id", l["user_id"]).single().execute()
            code  = db.table("referral_codes").select("code, owner_email").eq("id", l["referral_code_id"]).single().execute()
            referral_signups.append({
                "user_email":      user.data["email"] if user.data else "unknown",
                "affiliate_email": code.data["owner_email"] if code.data else "unknown",
                "code":            code.data["code"] if code.data else "unknown",
                "code_id":         l["referral_code_id"],
                "created_at":      l["created_at"]
            })
        except:
            pass

    # Affiliate bank accounts
    codes_raw = db.table("referral_codes").select("*").execute().data
    affiliate_balances = []
    for c in codes_raw:
        try:
            # Total earned
            earned = db.table("affiliate_earnings").select("amount").eq("affiliate_user_id", c["owner_user_id"]).execute()
            lifetime = sum(e["amount"] for e in earned.data)

            # Already paid
            paid_res = db.table("affiliate_payouts").select("amount").eq("affiliate_user_id", c["owner_user_id"]).execute()
            paid = sum(p["amount"] for p in paid_res.data) if paid_res.data else 0

            owed = round(lifetime - paid, 2)

            # Count referrals and sales
            links = db.table("referral_links").select("id").eq("referral_code_id", c["id"]).execute()
            sales = len([e for e in earned.data])

            affiliate_balances.append({
                "user_id":        c["owner_user_id"],
                "email":          c["owner_email"],
                "code":           c["code"],
                "lifetime":       lifetime,
                "owed":           max(owed, 0),
                "total_referrals": len(links.data),
                "total_sales":    sales
            })
        except:
            pass

    reviews = db.table("reviews").select("*").order("created_at", desc=True).execute().data

    return render_template_string(ADMIN_HTML,
        users=users, search=search, purchases=purchases,
        affiliate_earnings=affiliate_earnings,
        referral_signups=referral_signups,
        affiliate_balances=affiliate_balances,
        reviews=reviews,
        msg=msg, msg_type=msg_type, venmo=VENMO_HANDLE)


@app.route("/admin/approve-review", methods=["POST"])
def admin_approve_review():
    if not session.get("admin"):
        return redirect("/admin/login")
    review_id = request.form.get("review_id")
    db.table("reviews").update({"approved": True}).eq("id", review_id).execute()
    return redirect("/admin?msg=Review+approved&type=ok")


@app.route("/admin/unapprove-review", methods=["POST"])
def admin_unapprove_review():
    if not session.get("admin"):
        return redirect("/admin/login")
    review_id = request.form.get("review_id")
    db.table("reviews").update({"approved": False}).eq("id", review_id).execute()
    return redirect("/admin?msg=Review+unpublished&type=ok")


@app.route("/admin/delete-review", methods=["POST"])
def admin_delete_review():
    if not session.get("admin"):
        return redirect("/admin/login")
    review_id = request.form.get("review_id")
    db.table("reviews").delete().eq("id", review_id).execute()
    return redirect("/admin?msg=Review+deleted&type=ok")


@app.route("/admin/delete-referral-code", methods=["POST"])
def admin_delete_referral_code():
    if not session.get("admin"):
        return redirect("/admin/login")
    code_id = request.form.get("code_id")
    db.table("referral_codes").delete().eq("id", code_id).execute()
    return redirect("/admin?msg=Referral+code+deleted&type=ok")


@app.route("/admin/delete-user", methods=["POST"])
def admin_delete_user():
    if not session.get("admin"):
        return redirect("/admin/login")
    email = (request.form.get("email") or "").strip().lower()
    res = db.table("users").select("*").eq("email", email).execute()
    if not res.data:
        return redirect("/admin?msg=User+not+found&type=err")
    user_id = res.data[0]["id"]
    db.table("users").update({"deleted_at": datetime.utcnow().isoformat()}).eq("id", user_id).execute()
    print(f"✅ Admin deleted user {email}")
    return redirect(f"/admin?msg=User+{email}+deleted&type=ok")


@app.route("/admin/create-referral-code", methods=["POST"])
def admin_create_referral_code():
    if not session.get("admin"):
        return redirect("/admin/login")

    email = (request.form.get("email") or "").strip().lower()
    code  = (request.form.get("code") or "").strip().upper()

    if not email or not code:
        return redirect("/admin?msg=Email+and+code+required&type=err")

    res = db.table("users").select("*").eq("email", email).execute()
    if not res.data:
        return redirect("/admin?msg=User+not+found&type=err")

    user = res.data[0]

    existing = db.table("referral_codes").select("id").eq("code", code).execute()
    if existing.data:
        return redirect("/admin?msg=Code+already+exists&type=err")

    db.table("referral_codes").insert({
        "code": code,
        "owner_email": email,
        "owner_user_id": user["id"]
    }).execute()

    print(f"✅ Admin created referral code {code} for {email}")
    return redirect(f"/admin?msg=Created+code+{code}+for+{email}&type=ok")


@app.route("/admin/add-credits", methods=["POST"])
def admin_add_credits():
    if not session.get("admin"):
        return redirect("/admin/login")

    email       = (request.form.get("email") or "").strip().lower()
    credits     = int(request.form.get("credits") or 0)
    amount_paid = float(request.form.get("amount_paid") or 0)

    res = db.table("users").select("*").eq("email", email).execute()
    if not res.data:
        return redirect("/admin?msg=User+not+found&type=err")

    user        = res.data[0]
    new_credits = user["credits"] + credits
    db.table("users").update({"credits": new_credits}).eq("id", user["id"]).execute()
    purchase = db.table("purchases").insert({
        "user_id":       user["id"],
        "credits_added": credits,
        "amount_paid":   amount_paid,
        "stripe_session_id": "manual-venmo"
    }).execute()

    if purchase.data:
        pay_affiliate(user["id"], purchase.data[0]["id"], amount_paid)

    print(f"✅ Admin added {credits} credits to {email} (${amount_paid})")
    return redirect(f"/admin?msg=Added+{credits}+credits+to+{email}&type=ok&search={email}")


@app.route("/admin/pay-affiliate", methods=["POST"])
def admin_pay_affiliate():
    if not session.get("admin"):
        return redirect("/admin/login")

    affiliate_user_id = request.form.get("affiliate_user_id")
    amount            = float(request.form.get("amount") or 0)

    db.table("affiliate_payouts").insert({
        "affiliate_user_id": affiliate_user_id,
        "amount":            amount
    }).execute()

    print(f"✅ Paid affiliate {affiliate_user_id} ${amount}")
    return redirect("/admin?msg=Affiliate+marked+as+paid&type=ok")


ADMIN_LOGIN_HTML = """
<!DOCTYPE html>
<html>
<head><title>Admin Login</title>
<style>
  body { font-family: 'Segoe UI', sans-serif; background: #0f172a; display: flex; align-items: center; justify-content: center; min-height: 100vh; }
  .box { background: #1e293b; padding: 40px; border-radius: 16px; width: 320px; }
  h1 { color: #e8ff47; margin-bottom: 24px; font-size: 22px; }
  input { background: #0f172a; border: 1px solid #334155; border-radius: 8px; padding: 12px; color: #e2e8f0; width: 100%; margin-bottom: 12px; font-size: 14px; }
  button { background: #e8ff47; color: #000; border: none; border-radius: 8px; padding: 12px; width: 100%; font-weight: 700; cursor: pointer; font-size: 15px; }
  .err { color: #f87171; font-size: 13px; margin-bottom: 12px; }
</style>
</head>
<body>
<div class="box">
  <h1>⚡ Admin Login</h1>
  {% if error %}<div class="err">{{ error }}</div>{% endif %}
  <form method="POST">
    <input type="password" name="password" placeholder="Admin password" autofocus />
    <button type="submit">Login</button>
  </form>
</div>
</body>
</html>
"""

@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        if request.form.get("password") == ADMIN_PASSWORD:
            session["admin"] = True
            return redirect("/admin")
        return render_template_string(ADMIN_LOGIN_HTML, error="Wrong password")
    return render_template_string(ADMIN_LOGIN_HTML, error=None)

@app.route("/admin/logout")
def admin_logout():
    session.pop("admin", None)
    return redirect("/admin/login")


# ── Health ─────────────────────────────────────────────────────────────────────

@app.route("/", methods=["GET"])
def health():
    return ok({"status": "SnapSolve server running"})


if __name__ == "__main__":
    print("🚀 SnapSolve server starting on http://localhost:5000")
    print("   Admin panel: http://localhost:5000/admin\n")
    app.run(debug=True, port=5000)
