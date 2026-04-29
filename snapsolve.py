"""
SnapSolve — AI Quiz Helper
Login with your account, then press F2 to solve any question.

Requirements:
    pip install pillow keyboard requests

Usage:
    1. Run: python snapsolve.py
    2. Log in with your SnapSolve account
    3. Press F2, drag over the question, let go
    4. Escape to dismiss  •  F3 to quit
"""

import tkinter as tk
from tkinter import font as tkfont
import threading
import base64
import io
import sys
import json
import os

# ── Install check ──────────────────────────────────────────────────────────────
try:
    import keyboard
    import requests
    from PIL import ImageGrab, Image, ImageDraw
except ImportError as e:
    print(f"\n❌ Missing package: {e}")
    print("\nRun this to install everything:\n")
    print("    pip install pillow keyboard requests\n")
    sys.exit(1)

# ══════════════════════════════════════════════════════════════════════════════
#  CONFIG
# ══════════════════════════════════════════════════════════════════════════════
SERVER_URL   = "https://snapsolve-server.onrender.com"  # your live server
HOTKEY       = "f2"
SESSION_FILE = os.path.join(os.path.expanduser("~"), ".snapsolve_session")
# ══════════════════════════════════════════════════════════════════════════════


# ── Session storage (saves login so user doesn't retype every time) ────────────

def save_session(token, email):
    with open(SESSION_FILE, "w") as f:
        json.dump({"token": token, "email": email}, f)

def load_session():
    try:
        with open(SESSION_FILE, "r") as f:
            return json.load(f)
    except:
        return None

def clear_session():
    try: os.remove(SESSION_FILE)
    except: pass


# ── Login window ───────────────────────────────────────────────────────────────

class LoginWindow:

    def __init__(self, on_success):
        self.on_success = on_success

        self.root = tk.Tk()
        self.root.title("SnapSolve — Login")
        self.root.configure(bg="#0f172a")
        self.root.resizable(False, False)

        # Center window
        w, h = 360, 520
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        self.root.geometry(f"{w}x{h}+{(sw-w)//2}+{(sh-h)//2}")

        self._build()

        # Try auto-login from saved session
        session = load_session()
        if session:
            self._auto_login(session["token"], session["email"])

        self.root.mainloop()

    def _build(self):
        frame = tk.Frame(self.root, bg="#0f172a", padx=36, pady=32)
        frame.pack(fill=tk.BOTH, expand=True)

        # Logo
        tk.Label(
            frame, text="⚡", font=tkfont.Font(family="Segoe UI", size=40),
            bg="#0f172a", fg="#e8ff47"
        ).pack()

        tk.Label(
            frame, text="SNAPSOLVE",
            font=tkfont.Font(family="Segoe UI", size=20, weight="bold"),
            bg="#0f172a", fg="#f1f5f9"
        ).pack()

        tk.Label(
            frame, text="AI Quiz Helper",
            font=tkfont.Font(family="Segoe UI", size=11),
            bg="#0f172a", fg="#64748b"
        ).pack(pady=(2, 24))

        # Email
        tk.Label(frame, text="EMAIL", font=tkfont.Font(family="Segoe UI", size=9, weight="bold"),
            bg="#0f172a", fg="#64748b").pack(anchor="w")
        self.email_var = tk.StringVar()
        self.email_entry = tk.Entry(
            frame, textvariable=self.email_var,
            bg="#1e293b", fg="#f1f5f9", insertbackground="#f1f5f9",
            relief="flat", font=tkfont.Font(family="Segoe UI", size=12),
            bd=0
        )
        self.email_entry.pack(fill=tk.X, ipady=10, pady=(4, 14))
        self._add_border(frame, self.email_entry)

        # Password
        tk.Label(frame, text="PASSWORD", font=tkfont.Font(family="Segoe UI", size=9, weight="bold"),
            bg="#0f172a", fg="#64748b").pack(anchor="w")
        self.pass_var = tk.StringVar()
        self.pass_entry = tk.Entry(
            frame, textvariable=self.pass_var, show="•",
            bg="#1e293b", fg="#f1f5f9", insertbackground="#f1f5f9",
            relief="flat", font=tkfont.Font(family="Segoe UI", size=12),
            bd=0
        )
        self.pass_entry.pack(fill=tk.X, ipady=10, pady=(4, 6))
        self._add_border(frame, self.pass_entry)

        # Status message
        self.status_var = tk.StringVar()
        self.status_lbl = tk.Label(
            frame, textvariable=self.status_var,
            font=tkfont.Font(family="Segoe UI", size=10),
            bg="#0f172a", fg="#f87171", wraplength=280
        )
        self.status_lbl.pack(pady=(8, 0))

        # Login button
        self.login_btn = tk.Button(
            frame, text="LOGIN",
            font=tkfont.Font(family="Segoe UI", size=13, weight="bold"),
            bg="#e8ff47", fg="#000000", relief="flat",
            activebackground="#d4eb33", cursor="hand2",
            command=self._login
        )
        self.login_btn.pack(fill=tk.X, ipady=10, pady=(16, 8))

        # Register link
        reg_frame = tk.Frame(frame, bg="#0f172a")
        reg_frame.pack()
        tk.Label(reg_frame, text="No account?", bg="#0f172a", fg="#64748b",
            font=tkfont.Font(family="Segoe UI", size=10)).pack(side=tk.LEFT)
        reg_lnk = tk.Label(reg_frame, text=" Register", bg="#0f172a", fg="#3b82f6",
            font=tkfont.Font(family="Segoe UI", size=10, underline=True), cursor="hand2")
        reg_lnk.pack(side=tk.LEFT)
        reg_lnk.bind("<Button-1>", lambda e: self._show_register())

        # Bind enter key
        self.root.bind("<Return>", lambda e: self._login())
        self.email_entry.focus()

    def _add_border(self, parent, widget):
        """Draws a subtle border under an entry."""
        border = tk.Frame(parent, bg="#334155", height=1)
        border.pack(fill=tk.X, pady=(0, 0))

    def _set_status(self, msg, color="#f87171"):
        self.status_var.set(msg)
        self.status_lbl.config(fg=color)

    def _login(self):
        email    = self.email_var.get().strip()
        password = self.pass_var.get().strip()

        if not email or not password:
            self._set_status("Please enter your email and password.")
            return

        self.login_btn.config(state="disabled", text="Logging in...")
        self._set_status("")
        threading.Thread(target=self._do_login, args=(email, password), daemon=True).start()

    def _do_login(self, email, password):
        try:
            res = requests.post(f"{SERVER_URL}/login",
                json={"email": email, "password": password}, timeout=60)
            data = res.json()

            if res.status_code == 200:
                save_session(data["token"], data["email"])
                self.root.after(0, lambda: self._success(data["token"], data["email"], data["credits"]))
            else:
                msg = data.get("error", "Login failed.")
                self.root.after(0, lambda: self._set_status(msg))
                self.root.after(0, lambda: self.login_btn.config(state="normal", text="LOGIN"))
        except Exception as e:
            self.root.after(0, lambda: self._set_status("Can't reach server. Is it running?"))
            self.root.after(0, lambda: self.login_btn.config(state="normal", text="LOGIN"))

    def _auto_login(self, token, email):
        """Verify saved token is still valid."""
        def check():
            try:
                res = requests.get(f"{SERVER_URL}/me",
                    headers={"Authorization": f"Bearer {token}"}, timeout=5)
                if res.status_code == 200:
                    data = res.json()
                    self.root.after(0, lambda: self._success(token, email, data["credits"]))
                else:
                    clear_session()
            except:
                pass  # server offline, just show login screen
        threading.Thread(target=check, daemon=True).start()

    def _success(self, token, email, credits):
        self.root.destroy()
        self.on_success(token, email, credits)

    def _show_register(self):
        RegisterWindow(self.root, lambda token, email, credits: self._success(token, email, credits))


# ── Register window ────────────────────────────────────────────────────────────

class RegisterWindow:

    def __init__(self, parent, on_success):
        self.on_success = on_success

        self.win = tk.Toplevel(parent)
        self.win.title("SnapSolve — Register")
        self.win.configure(bg="#0f172a")
        self.win.resizable(False, False)

        w, h = 360, 540
        sw = self.win.winfo_screenwidth()
        sh = self.win.winfo_screenheight()
        self.win.geometry(f"{w}x{h}+{(sw-w)//2}+{(sh-h)//2}")
        self.win.grab_set()

        self._build()

    def _build(self):
        frame = tk.Frame(self.win, bg="#0f172a", padx=36, pady=32)
        frame.pack(fill=tk.BOTH, expand=True)

        tk.Label(frame, text="Create Account",
            font=tkfont.Font(family="Segoe UI", size=18, weight="bold"),
            bg="#0f172a", fg="#f1f5f9").pack(pady=(0, 4))

        tk.Label(frame, text="Get started with SnapSolve",
            font=tkfont.Font(family="Segoe UI", size=11),
            bg="#0f172a", fg="#64748b").pack(pady=(0, 24))

        tk.Label(frame, text="EMAIL", font=tkfont.Font(family="Segoe UI", size=9, weight="bold"),
            bg="#0f172a", fg="#64748b").pack(anchor="w")
        self.email_var = tk.StringVar()
        tk.Entry(frame, textvariable=self.email_var,
            bg="#1e293b", fg="#f1f5f9", insertbackground="#f1f5f9",
            relief="flat", font=tkfont.Font(family="Segoe UI", size=12)
        ).pack(fill=tk.X, ipady=10, pady=(4, 14))

        tk.Label(frame, text="PASSWORD", font=tkfont.Font(family="Segoe UI", size=9, weight="bold"),
            bg="#0f172a", fg="#64748b").pack(anchor="w")
        self.pass_var = tk.StringVar()
        tk.Entry(frame, textvariable=self.pass_var, show="•",
            bg="#1e293b", fg="#f1f5f9", insertbackground="#f1f5f9",
            relief="flat", font=tkfont.Font(family="Segoe UI", size=12)
        ).pack(fill=tk.X, ipady=10, pady=(4, 14))

        tk.Label(frame, text="CONFIRM PASSWORD", font=tkfont.Font(family="Segoe UI", size=9, weight="bold"),
            bg="#0f172a", fg="#64748b").pack(anchor="w")
        self.pass2_var = tk.StringVar()
        tk.Entry(frame, textvariable=self.pass2_var, show="•",
            bg="#1e293b", fg="#f1f5f9", insertbackground="#f1f5f9",
            relief="flat", font=tkfont.Font(family="Segoe UI", size=12)
        ).pack(fill=tk.X, ipady=10, pady=(4, 6))

        self.status_var = tk.StringVar()
        tk.Label(frame, textvariable=self.status_var,
            font=tkfont.Font(family="Segoe UI", size=10),
            bg="#0f172a", fg="#f87171", wraplength=280).pack(pady=(8, 0))

        self.reg_btn = tk.Button(frame, text="CREATE ACCOUNT",
            font=tkfont.Font(family="Segoe UI", size=13, weight="bold"),
            bg="#e8ff47", fg="#000000", relief="flat",
            activebackground="#d4eb33", cursor="hand2",
            command=self._register)
        self.reg_btn.pack(fill=tk.X, ipady=10, pady=(16, 0))

        self.win.bind("<Return>", lambda e: self._register())

    def _register(self):
        email = self.email_var.get().strip()
        pw1   = self.pass_var.get()
        pw2   = self.pass2_var.get()

        if not email or not pw1:
            self.status_var.set("Please fill in all fields.")
            return
        if pw1 != pw2:
            self.status_var.set("Passwords don't match.")
            return
        if len(pw1) < 6:
            self.status_var.set("Password must be at least 6 characters.")
            return

        self.reg_btn.config(state="disabled", text="Creating account...")
        threading.Thread(target=self._do_register, args=(email, pw1), daemon=True).start()

    def _do_register(self, email, password):
        try:
            res  = requests.post(f"{SERVER_URL}/register",
                json={"email": email, "password": password}, timeout=60)
            data = res.json()

            if res.status_code == 200:
                save_session(data["token"], data["email"])
                self.win.after(0, lambda: self._success(data["token"], data["email"], data["credits"]))
            else:
                msg = data.get("error", "Registration failed.")
                self.win.after(0, lambda: self.status_var.set(msg))
                self.win.after(0, lambda: self.reg_btn.config(state="normal", text="CREATE ACCOUNT"))
        except:
            self.win.after(0, lambda: self.status_var.set("Can't reach server. Is it running?"))
            self.win.after(0, lambda: self.reg_btn.config(state="normal", text="CREATE ACCOUNT"))

    def _success(self, token, email, credits):
        self.win.destroy()
        self.on_success(token, email, credits)


# ── Region selector ────────────────────────────────────────────────────────────

class RegionSelector:

    def __init__(self, callback):
        self.callback = callback
        self.start_x = self.start_y = 0
        self.rect = None

        self.root = tk.Toplevel()
        self.root.attributes("-fullscreen", True)
        self.root.attributes("-alpha", 0.25)
        self.root.attributes("-topmost", True)
        self.root.configure(bg="black")
        self.root.config(cursor="crosshair")

        self.canvas = tk.Canvas(self.root, bg="black", highlightthickness=0)
        self.canvas.pack(fill=tk.BOTH, expand=True)

        self.canvas.bind("<ButtonPress-1>",   self.on_press)
        self.canvas.bind("<B1-Motion>",       self.on_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_release)
        self.root.bind("<Escape>", lambda e: self.root.destroy())

        self.canvas.create_text(
            self.root.winfo_screenwidth() // 2, 40,
            text="Drag over the question   •   Esc to cancel",
            fill="white", font=("Segoe UI", 16)
        )

    def on_press(self, e):
        self.start_x, self.start_y = e.x, e.y
        if self.rect: self.canvas.delete(self.rect)

    def on_drag(self, e):
        if self.rect: self.canvas.delete(self.rect)
        self.rect = self.canvas.create_rectangle(
            self.start_x, self.start_y, e.x, e.y,
            outline="#3b82f6", width=2, fill="#3b82f6", stipple="gray25"
        )

    def on_release(self, e):
        x1 = min(self.start_x, e.x)
        y1 = min(self.start_y, e.y)
        x2 = max(self.start_x, e.x)
        y2 = max(self.start_y, e.y)
        self.root.destroy()
        if x2 - x1 > 10 and y2 - y1 > 10:
            self.callback(x1, y1, x2, y2)


# ── Answer overlay ─────────────────────────────────────────────────────────────

class AnswerOverlay:

    def __init__(self, root, get_credits):
        self.root        = root
        self.get_credits = get_credits
        self.win         = None
        self.visible     = False

    def show_loading(self):
        self._build()
        self._set("⚡", "THINKING...", "Reading your question...", "", loading=True)
        self.visible = True

    def show_answer(self, q_type, letter, explanation, confidence, credits):
        if not self.win or not tk.Toplevel.winfo_exists(self.win):
            self._build()
        conf_text = f"Confidence: {confidence}%  •  {credits} credits left"
        if q_type == "open_ended":
            self._set(letter, "OPEN ENDED", explanation, f"{credits} credits left", open_ended=True)
        else:
            self._set(letter, "BEST ANSWER", explanation, conf_text)
        self.visible = True

    def show_error(self, msg):
        if not self.win or not tk.Toplevel.winfo_exists(self.win):
            self._build()
        self._set("!", "ERROR", msg, "", error=True)
        self.visible = True

    def hide(self):
        if self.win and tk.Toplevel.winfo_exists(self.win):
            self.win.destroy()
        self.visible = False

    def _build(self):
        if self.win and tk.Toplevel.winfo_exists(self.win):
            self.win.destroy()

        self.win = tk.Toplevel(self.root)
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.attributes("-alpha", 0.95)
        self.win.configure(bg="#0f172a")

        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        self.win.geometry(f"320x175+{sw - 344}+{sh - 235}")

        self.win.bind("<ButtonPress-1>", self._drag_start)
        self.win.bind("<B1-Motion>",     self._drag_move)

        self.frame = tk.Frame(self.win, bg="#0f172a", padx=16, pady=14)
        self.frame.pack(fill=tk.BOTH, expand=True)

        top = tk.Frame(self.frame, bg="#0f172a")
        top.pack(fill=tk.X)

        self.badge = tk.Label(
            top, text="?", width=3,
            font=tkfont.Font(family="Segoe UI", size=28, weight="bold"),
            bg="#2563eb", fg="white"
        )
        self.badge.pack(side=tk.LEFT, padx=(0, 12))

        right = tk.Frame(top, bg="#0f172a")
        right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.title_lbl = tk.Label(right, text="BEST ANSWER",
            font=tkfont.Font(family="Segoe UI", size=9, weight="bold"),
            bg="#0f172a", fg="#64748b")
        self.title_lbl.pack(anchor="w")

        self.conf_lbl = tk.Label(right, text="",
            font=tkfont.Font(family="Segoe UI", size=10),
            bg="#0f172a", fg="#94a3b8")
        self.conf_lbl.pack(anchor="w")

        self.exp_lbl = tk.Label(self.frame, text="", wraplength=288, justify="left",
            font=tkfont.Font(family="Segoe UI", size=10),
            bg="#0f172a", fg="#e2e8f0")
        self.exp_lbl.pack(fill=tk.X, pady=(8, 0))

        tk.Label(self.frame,
            text=f"Press {HOTKEY.upper()} or Esc to close",
            font=tkfont.Font(family="Segoe UI", size=8),
            bg="#0f172a", fg="#334155"
        ).pack(side=tk.BOTTOM, anchor="e")

    def _set(self, letter, title, explanation, conf,
             loading=False, error=False, open_ended=False):
        color = "#2563eb"
        if loading:    color = "#7c3aed"
        if error:      color = "#dc2626"
        if open_ended: color = "#0e7490"

        if open_ended:
            self.badge.config(text=letter, bg=color,
                font=tkfont.Font(family="Segoe UI", size=14, weight="bold"),
                wraplength=260, width=0)
            self.win.geometry("")
        else:
            self.badge.config(text=letter, bg=color,
                font=tkfont.Font(family="Segoe UI", size=28, weight="bold"),
                wraplength=0, width=3)
            sw = self.root.winfo_screenwidth()
            sh = self.root.winfo_screenheight()
            self.win.geometry(f"320x175+{sw - 344}+{sh - 235}")

        self.title_lbl.config(text=title)
        self.exp_lbl.config(text=explanation)
        self.conf_lbl.config(text=conf)

    def _drag_start(self, e): self._dx, self._dy = e.x, e.y
    def _drag_move(self, e):
        x = self.win.winfo_x() + e.x - self._dx
        y = self.win.winfo_y() + e.y - self._dy
        self.win.geometry(f"+{x}+{y}")


# ── Settings file ──────────────────────────────────────────────────────────────
SETTINGS_FILE = os.path.join(os.path.expanduser("~"), ".snapsolve_settings")

def load_settings():
    try:
        with open(SETTINGS_FILE, "r") as f:
            return json.load(f)
    except:
        return {"hotkey": "f2", "gear_x": None, "gear_y": None}

def save_settings(settings):
    with open(SETTINGS_FILE, "w") as f:
        json.dump(settings, f)


# ── Floating gear panel ────────────────────────────────────────────────────────

class GearPanel:
    """Always-on-top floating gear icon that expands into a settings panel."""

    GEAR_PATH = "M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6z"

    def __init__(self, root, email, get_credits, on_quit, on_switch, on_hotkey_change):
        self.root             = root
        self.email            = email
        self.get_credits      = get_credits
        self.on_quit          = on_quit
        self.on_switch        = on_switch
        self.on_hotkey_change = on_hotkey_change
        self.expanded         = False
        self.settings         = load_settings()
        self._drag_x          = 0
        self._drag_y          = 0
        self._rebinding       = False

        self._build()

    def _build(self):
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()

        # Default position top-right
        gx = self.settings.get("gear_x") or sw - 70
        gy = self.settings.get("gear_y") or 30

        self.win = tk.Toplevel(self.root)
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.attributes("-alpha", 0.95)
        self.win.configure(bg="#0f172a")
        self.win.geometry(f"+{gx}+{gy}")

        self._build_icon()

    def _build_icon(self):
        """The small gear button."""
        for w in self.win.winfo_children():
            w.destroy()
        self.expanded = False
        self.win.geometry("44x44")

        canvas = tk.Canvas(self.win, width=44, height=44, bg="#0f172a",
                           highlightthickness=0, cursor="hand2")
        canvas.pack()

        import math
        cx, cy = 22, 22
        r_out, r_in, teeth = 13, 9, 8
        points = []
        for i in range(teeth * 2):
            angle = math.pi * i / teeth - math.pi / 2
            a1 = angle - math.pi / (teeth * 2.5)
            a2 = angle + math.pi / (teeth * 2.5)
            r  = r_out if i % 2 == 0 else r_in
            if i % 2 == 0:
                points.extend([cx + r_out * math.cos(a1), cy + r_out * math.sin(a1)])
                points.extend([cx + r_out * math.cos(a2), cy + r_out * math.sin(a2)])
            else:
                points.extend([cx + r_in * math.cos(a1), cy + r_in * math.sin(a1)])
                points.extend([cx + r_in * math.cos(a2), cy + r_in * math.sin(a2)])

        canvas.create_oval(2, 2, 42, 42, fill="#1e293b", outline="#e8ff47", width=2)
        canvas.create_polygon(points, fill="#e8ff47", outline="#e8ff47")
        canvas.create_oval(cx-5, cy-5, cx+5, cy+5, fill="#1e293b", outline="#1e293b")

        canvas.bind("<ButtonPress-1>",   self._drag_start)
        canvas.bind("<B1-Motion>",       self._drag_move)
        canvas.bind("<ButtonRelease-1>", self._drag_end)
        self._drag_moved = False

    def _build_panel(self):
        """The expanded settings panel."""
        for w in self.win.winfo_children():
            w.destroy()
        self.expanded = True
        self.win.geometry("248x380")

        # Header / drag handle
        header = tk.Frame(self.win, bg="#0a1220", padx=14, pady=10)
        header.pack(fill=tk.X)
        header.bind("<ButtonPress-1>",   self._drag_start)
        header.bind("<B1-Motion>",       self._drag_move)
        header.bind("<ButtonRelease-1>", self._drag_end)

        # Drag dots
        dots = tk.Label(header, text="⠿",
            font=tkfont.Font(family="Segoe UI", size=14),
            bg="#0a1220", fg="#334155", cursor="fleur")
        dots.pack(side=tk.LEFT, padx=(0, 6))
        dots.bind("<ButtonPress-1>",   self._drag_start)
        dots.bind("<B1-Motion>",       self._drag_move)
        dots.bind("<ButtonRelease-1>", self._drag_end)

        tk.Label(header, text="SNAP TUTOR",
            font=tkfont.Font(family="Segoe UI", size=10, weight="bold"),
            bg="#0a1220", fg="#e8ff47").pack(side=tk.LEFT)

        close_btn = tk.Label(header, text="×", font=tkfont.Font(family="Segoe UI", size=16),
            bg="#0a1220", fg="#5a6a80", cursor="hand2")
        close_btn.pack(side=tk.RIGHT)
        close_btn.bind("<Button-1>", lambda e: self._build_icon())

        body = tk.Frame(self.win, bg="#0d1826", padx=14, pady=12)
        body.pack(fill=tk.BOTH, expand=True)

        # Account
        self._section(body, "LOGGED IN AS")
        tk.Label(body, text=self.email,
            font=tkfont.Font(family="Segoe UI", size=12, weight="bold"),
            bg="#0a1220", fg="#e8edf5", anchor="w",
            wraplength=210).pack(fill=tk.X, pady=(0, 10))

        # Credits
        self._section(body, "CREDITS")
        cred_row = tk.Frame(body, bg="#0a1220")
        cred_row.pack(fill=tk.X, pady=(0, 10))

        self.credits_lbl = tk.Label(cred_row,
            text=str(self.get_credits()),
            font=tkfont.Font(family="Segoe UI", size=22, weight="bold"),
            bg="#0a1220", fg="#e8ff47")
        self.credits_lbl.pack(side=tk.LEFT)

        buy_lbl = tk.Label(cred_row, text="Buy more →",
            font=tkfont.Font(family="Segoe UI", size=10),
            bg="#0a1220", fg="#3b82f6", cursor="hand2")
        buy_lbl.pack(side=tk.RIGHT, padx=4)
        buy_lbl.bind("<Button-1>", lambda e: self._open_buy())

        # Hotkey
        self._section(body, "HOTKEY")
        hk_row = tk.Frame(body, bg="#0a1220")
        hk_row.pack(fill=tk.X, pady=(0, 10))

        self.hk_lbl = tk.Label(hk_row,
            text=self.settings.get("hotkey", "f2").upper(),
            font=tkfont.Font(family="Courier New", size=13, weight="bold"),
            bg="#1e2a3a", fg="#e8edf5", width=6, relief="flat")
        self.hk_lbl.pack(side=tk.LEFT, padx=(0, 8))

        self.hk_btn = tk.Button(hk_row, text="Change",
            font=tkfont.Font(family="Segoe UI", size=10),
            bg="#0a1220", fg="#94a3b8",
            relief="flat", bd=1, cursor="hand2",
            command=self._start_rebind)
        self.hk_btn.pack(side=tk.LEFT)

        self.hk_hint = tk.Label(body, text="Press any key...",
            font=tkfont.Font(family="Segoe UI", size=10),
            bg="#0d1826", fg="#e8ff47")

        # Divider
        tk.Frame(body, bg="#1e2a3a", height=1).pack(fill=tk.X, pady=8)

        # Switch account
        sw_btn = tk.Button(body, text="Switch account",
            font=tkfont.Font(family="Segoe UI", size=12),
            bg="#0d1826", fg="#94a3b8",
            relief="flat", anchor="w", cursor="hand2",
            command=self._switch_account)
        sw_btn.pack(fill=tk.X, pady=(0, 6))

        # Quit
        quit_btn = tk.Button(body, text="Quit Snap Tutor",
            font=tkfont.Font(family="Segoe UI", size=12),
            bg="#0d1826", fg="#f87171",
            relief="flat", anchor="w", cursor="hand2",
            command=self._quit)
        quit_btn.pack(fill=tk.X)

    def _section(self, parent, label):
        tk.Label(parent, text=label,
            font=tkfont.Font(family="Segoe UI", size=9, weight="bold"),
            bg="#0a1220" if label != "HOTKEY" else "#0d1826",
            fg="#5a6a80").pack(anchor="w", pady=(0, 4))

    def _open_buy(self):
        import webbrowser
        webbrowser.open("https://thesnaptutor.netlify.app/#pricing")

    def _start_rebind(self):
        self._rebinding = True
        self.hk_btn.config(text="Cancel", command=self._cancel_rebind)
        self.hk_hint.pack(anchor="w")
        self.win.bind("<Key>", self._on_key)
        self.win.focus_force()

    def _cancel_rebind(self):
        self._rebinding = False
        self.hk_hint.pack_forget()
        self.hk_btn.config(text="Change", command=self._start_rebind)
        self.win.unbind("<Key>")

    def _on_key(self, e):
        if not self._rebinding:
            return
        new_key = e.keysym.lower()
        if new_key in ("escape", "return"):
            self._cancel_rebind()
            return
        # Update hotkey
        self.on_hotkey_change(new_key)
        self.settings["hotkey"] = new_key
        save_settings(self.settings)
        self.hk_lbl.config(text=new_key.upper())
        self._cancel_rebind()

    def _switch_account(self):
        clear_session()
        self.on_switch()

    def _quit(self):
        self.on_quit()

    def update_credits(self, credits):
        if self.expanded and hasattr(self, "credits_lbl"):
            self.credits_lbl.config(text=str(credits))

    def _drag_start(self, e):
        self._drag_x     = e.x_root - self.win.winfo_x()
        self._drag_y     = e.y_root - self.win.winfo_y()
        self._drag_moved = False

    def _drag_move(self, e):
        x = e.x_root - self._drag_x
        y = e.y_root - self._drag_y
        self.win.geometry(f"+{x}+{y}")
        self._drag_moved = True

    def _drag_end(self, e):
        # Save position
        self.settings["gear_x"] = self.win.winfo_x()
        self.settings["gear_y"] = self.win.winfo_y()
        save_settings(self.settings)
        # If barely moved, treat as click
        if not self._drag_moved:
            if self.expanded:
                self._build_icon()
            else:
                self._build_panel()


# ── Main app ───────────────────────────────────────────────────────────────────

class SnapSolve:

    def __init__(self, token, email, credits):
        self.token   = token
        self.email   = email
        self.credits = credits
        self.busy    = False

        self.root    = tk.Tk()
        self.root.withdraw()
        self.overlay = AnswerOverlay(self.root, lambda: self.credits)

        # Load saved settings
        self.settings = load_settings()
        global HOTKEY
        HOTKEY = self.settings.get("hotkey", "f2")

        # Register hotkeys
        keyboard.add_hotkey(HOTKEY, self.on_hotkey, suppress=True)
        keyboard.add_hotkey("escape", self.on_escape)

        # Floating gear panel
        self.gear = GearPanel(
            self.root, email,
            get_credits      = lambda: self.credits,
            on_quit          = self.quit_app,
            on_switch        = self.switch_account,
            on_hotkey_change = self.change_hotkey
        )

        print(f"✅ Logged in as {email} — {credits} credits")
        print(f"   Press {HOTKEY.upper()} to capture a question\n")

        self.root.mainloop()

    def on_hotkey(self):
        if self.overlay.visible:
            self.root.after(0, self.overlay.hide)
            return
        if self.busy:
            return
        self.root.after(0, self._start_capture)

    def on_escape(self):
        self.root.after(0, self.overlay.hide)

    def quit_app(self):
        import os as _os
        _os.kill(_os.getpid(), 9)

    def switch_account(self):
        clear_session()
        self.root.after(0, self._relaunch_login)

    def _relaunch_login(self):
        self.root.destroy()
        def on_login(token, email, credits):
            SnapSolve(token, email, credits)
        LoginWindow(on_login)

    def change_hotkey(self, new_key):
        global HOTKEY
        try:
            keyboard.remove_hotkey(HOTKEY)
        except:
            pass
        HOTKEY = new_key
        keyboard.add_hotkey(HOTKEY, self.on_hotkey, suppress=True)
        print(f"✅ Hotkey changed to {HOTKEY.upper()}")

    def _start_capture(self):
        selector = RegionSelector(self._on_region)
        selector.root.mainloop()

    def _on_region(self, x1, y1, x2, y2):
        self.busy = True
        self.overlay.show_loading()
        threading.Thread(target=self._solve, args=(x1, y1, x2, y2), daemon=True).start()

    def _solve(self, x1, y1, x2, y2):
        try:
            screenshot = ImageGrab.grab(bbox=(x1, y1, x2, y2))
            buf = io.BytesIO()
            screenshot.save(buf, format="PNG")
            img_b64 = base64.b64encode(buf.getvalue()).decode()

            res = requests.post(
                f"{SERVER_URL}/solve",
                json={"image": img_b64},
                headers={"Authorization": f"Bearer {self.token}"},
                timeout=60
            )
            data = res.json()

            if res.status_code == 402:
                self.root.after(0, lambda: self.overlay.show_error(
                    "No credits left! Visit the website to buy more."))
                return

            if res.status_code != 200:
                msg = data.get("error", "Server error.")
                self.root.after(0, lambda: self.overlay.show_error(msg))
                return

            raw          = data["raw"]
            self.credits = data["credits_remaining"]
            self.root.after(0, lambda: self.gear.update_credits(self.credits))
            q_type, letter, explanation, confidence = self._parse(raw)

            print(f"\n--- Response ---\n{raw}\n----------------\n")
            self.root.after(0, lambda: self.overlay.show_answer(
                q_type, letter, explanation, confidence, self.credits))

        except requests.exceptions.ConnectionError:
            self.root.after(0, lambda: self.overlay.show_error(
                "Can't reach server. Check your connection."))
        except Exception as e:
            self.root.after(0, lambda: self.overlay.show_error(str(e)[:200]))
        finally:
            self.busy = False

    def _parse(self, raw):
        q_type      = "multiple_choice"
        letter      = None
        explanation = ""
        confidence  = 90

        for line in raw.splitlines():
            line = line.strip()
            if line.upper().startswith("TYPE:"):
                if "open" in line.split(":", 1)[1].strip().lower():
                    q_type = "open_ended"
            elif line.upper().startswith("ANSWER:"):
                letter = line.split(":", 1)[1].strip()
                if q_type == "multiple_choice":
                    letter = letter.upper()[:1]
            elif line.upper().startswith("EXPLANATION:"):
                explanation = line.split(":", 1)[1].strip()
            elif line.upper().startswith("CONFIDENCE:"):
                try: confidence = int(line.split(":", 1)[1].strip().rstrip("%"))
                except: pass

        if q_type == "multiple_choice":
            if letter not in ("A", "B", "C", "D"):
                for line in raw.splitlines():
                    for token in line.upper().split():
                        if token.strip(".:,)(") in ("A", "B", "C", "D"):
                            letter = token.strip(".:,)(")
                            break
                    if letter in ("A", "B", "C", "D"):
                        break
            if letter not in ("A", "B", "C", "D"):
                letter = "?"
                explanation = "Couldn't parse A/B/C/D. Try again."

        if q_type == "open_ended" and not letter:
            letter = "?"
            explanation = "Couldn't find an answer. Try again."

        return q_type, letter, explanation, confidence


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    def on_login(token, email, credits):
        SnapSolve(token, email, credits)

    LoginWindow(on_login)
