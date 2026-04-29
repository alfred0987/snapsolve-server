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
        "email": email, "password_hash": hashed, "credits": 0
    }).execute()

    user  = res.data[0]
    token = make_token(user["id"])
    print(f"✅ Registered: {email}")
    return ok({"token": token, "credits": 0, "email": email})


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


@app.route("/me", methods=["GET"])
def me():
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
        "pay_currency":     "usdttrc20",
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
        db.table("purchases").insert({
            "user_id":       user_id,
            "credits_added": credits,
            "amount_paid":   amount,
            "stripe_session_id": data.get("payment_id", "crypto")
        }).execute()

        print(f"💰 Crypto payment: user {user_id} +{credits} credits ({pack})")

    return ok({"received": True})


# ── Admin panel ────────────────────────────────────────────────────────────────

ADMIN_HTML = """
<!DOCTYPE html>
<html>
<head>
<title>SnapSolve Admin</title>
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
  .msg { padding: 12px; border-radius: 8px; margin-bottom: 16px; font-size: 14px; }
  .msg.ok  { background: #14532d; color: #86efac; }
  .msg.err { background: #7f1d1d; color: #fca5a5; }
  table { width: 100%; border-collapse: collapse; font-size: 13px; }
  th { text-align: left; color: #64748b; padding: 8px 12px; border-bottom: 1px solid #334155; }
  td { padding: 10px 12px; border-bottom: 1px solid #1e293b; }
  tr:hover td { background: #1e293b; }
  .badge { background: #2563eb; color: #fff; border-radius: 4px; padding: 2px 8px; font-size: 12px; font-weight: 700; }
  .row { display: flex; gap: 12px; }
  .row input { flex: 1; }
  .venmo { background: #1e3a5f; border-radius: 8px; padding: 16px; margin-bottom: 16px; }
  .venmo strong { color: #60a5fa; }
</style>
</head>
<body>
<h1>⚡ SnapSolve Admin</h1>
<p class="sub">Manage users, credits, and manual payments</p>

{% if msg %}
<div class="msg {{ msg_type }}">{{ msg }}</div>
{% endif %}

<!-- Venmo reminder -->
<div class="card">
  <h2>📱 Venmo Manual Payments</h2>
  <div class="venmo">
    Your Venmo: <strong>{{ venmo }}</strong><br><br>
    When someone pays manually, search their email below and add their credits.
  </div>
  <form method="POST" action="/admin/add-credits">
    <div class="row">
      <input name="email" placeholder="Customer email" required />
      <input name="credits" type="number" placeholder="Credits to add" required style="max-width:160px" />
    </div>
    <input name="note" placeholder="Note (e.g. Venmo payment $5 — 100 credits)" />
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
    <tr><th>Email</th><th>Credits</th><th>Joined</th></tr>
    {% for u in users %}
    <tr>
      <td>{{ u.email }}</td>
      <td><span class="badge">{{ u.credits }}</span></td>
      <td>{{ u.created_at[:10] }}</td>
    </tr>
    {% endfor %}
  </table>
  {% endif %}
</div>

<!-- Recent purchases -->
<div class="card">
  <h2>💰 Recent Purchases</h2>
  <table>
    <tr><th>User</th><th>Credits</th><th>Amount</th><th>Date</th></tr>
    {% for p in purchases %}
    <tr>
      <td>{{ p.user_id[:8] }}...</td>
      <td>+{{ p.credits_added }}</td>
      <td>${{ p.amount_paid }}</td>
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

    search    = request.args.get("search", "")
    msg       = request.args.get("msg", "")
    msg_type  = request.args.get("type", "ok")

    users = []
    if search:
        res   = db.table("users").select("*").ilike("email", f"%{search}%").execute()
        users = res.data

    purchases = db.table("purchases").select("*").order("created_at", desc=True).limit(20).execute().data

    return render_template_string(ADMIN_HTML,
        users=users, search=search, purchases=purchases,
        msg=msg, msg_type=msg_type, venmo=VENMO_HANDLE)


@app.route("/admin/add-credits", methods=["POST"])
def admin_add_credits():
    if not session.get("admin"):
        return redirect("/admin/login")

    email   = (request.form.get("email") or "").strip().lower()
    credits = int(request.form.get("credits") or 0)

    res = db.table("users").select("*").eq("email", email).execute()
    if not res.data:
        return redirect("/admin?msg=User+not+found&type=err")

    user        = res.data[0]
    new_credits = user["credits"] + credits
    db.table("users").update({"credits": new_credits}).eq("id", user["id"]).execute()
    db.table("purchases").insert({
        "user_id":       user["id"],
        "credits_added": credits,
        "amount_paid":   0,
        "stripe_session_id": "manual-venmo"
    }).execute()

    print(f"✅ Admin added {credits} credits to {email}")
    return redirect(f"/admin?msg=Added+{credits}+credits+to+{email}&type=ok&search={email}")


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
