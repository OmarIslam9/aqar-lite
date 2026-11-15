from tests.conftest import PASSWORD, _token, csrf, login

GENERIC = "البريد الإلكتروني أو كلمة المرور غير صحيحة"


def _attempt(c, email, password):
    token = _token(c.get("/login").get_data(as_text=True))
    return c.post("/login", data={"email": email, "password": password, "csrf_token": token})


def test_security_headers_and_cookie_flags(app):
    c = app.test_client()
    h = c.get("/login").headers
    assert h["X-Content-Type-Options"] == "nosniff" and h["X-Frame-Options"] == "DENY"
    assert "default-src 'self'" in h["Content-Security-Policy"]
    assert h["Cache-Control"] == "no-store"

    resp = _attempt(c, "manager1@nakheel.test", PASSWORD)
    sid = next(x for x in resp.headers.getlist("Set-Cookie") if x.startswith("sid="))
    assert "HttpOnly" in sid and "SameSite=Lax" in sid


def test_login_error_does_not_reveal_whether_email_exists(app):
    c = app.test_client()
    known = _attempt(c, "manager1@nakheel.test", "wrong-password")
    unknown = _attempt(c, "nobody@nowhere.test", "wrong-password")
    assert known.status_code == unknown.status_code == 401
    assert GENERIC in known.get_data(as_text=True) and GENERIC in unknown.get_data(as_text=True)


def test_login_is_throttled_after_repeated_failures(app):
    c = app.test_client()
    codes = [_attempt(c, "accountant1@nakheel.test", "x").status_code for _ in range(6)]
    assert codes[:5] == [401] * 5 and codes[5] == 429
    assert _attempt(c, "accountant1@nakheel.test", PASSWORD).status_code == 429   # حتى الباسورد الصح


def test_logout_invalidates_the_session_on_the_server(app):
    c = app.test_client()
    login(c, "manager1@nakheel.test")
    stolen = c.get_cookie("sid").value
    assert c.post("/logout", data={"csrf_token": csrf(c)}).status_code == 302

    attacker = app.test_client()
    attacker.set_cookie("sid", stolen)
    assert attacker.get("/").status_code == 302          # الكوكي القديم لم يعد يعمل


def test_post_without_csrf_token_is_rejected(app):
    c = app.test_client()
    login(c, "manager1@nakheel.test")
    assert c.post("/logout").status_code == 403