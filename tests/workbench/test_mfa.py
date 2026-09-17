"""The second factor: enrolment, the two-step sign-in, replay, and what it must never leak.

A password proves knowledge of a secret; it does not prove the person typing it owns the account.
The tests here are the ones that decide whether the second factor is real:

* is the password still checked first, so a wrong password never reveals that MFA is on;
* is a code single-use, so one observed over a shoulder is worthless a moment later;
* does a failed second step leave a usable token behind.
"""
from __future__ import annotations

import time

import pytest

from workbench.security import totp
from workbench.security.auth import AuthError, AuthService, MfaRequired
from workbench.security.totp import TotpError

REQUIRED = ["manager", "admin"]


@pytest.fixture
def auth(tmp_path) -> AuthService:
    return AuthService(tmp_path / "sec", seed_default=True)


@pytest.fixture
def enrolled(auth) -> tuple[AuthService, str]:
    """An admin account with a live authenticator, and its secret."""
    begin = auth.begin_enrolment("admin")
    auth.confirm_enrolment("admin", totp.code_now(begin["secret"]))
    return auth, begin["secret"]


# ------------------------------------------------------------------ the algorithm


class TestCodes:
    def test_a_code_is_six_digits_and_changes_every_thirty_seconds(self):
        secret = totp.new_secret()
        now = 1_700_000_000
        code = totp.code_now(secret, now)
        assert len(code) == totp.DIGITS and code.isdigit()
        assert totp.code_now(secret, now + totp.STEP_SECONDS) != code
        assert totp.code_now(secret, now + 5) == code, "the code is stable inside its step"

    def test_a_drifting_clock_is_tolerated_but_a_stale_code_is_not(self):
        secret = totp.new_secret()
        now = 1_700_000_000
        assert totp.verify(secret, totp.code_now(secret, now - totp.STEP_SECONDS), at=now)
        with pytest.raises(TotpError):
            totp.verify(secret, totp.code_now(secret, now - 4 * totp.STEP_SECONDS), at=now)

    @pytest.mark.parametrize("bad", ["", "12345", "1234567", "abcdef", "12 34 56x", None])
    def test_a_malformed_code_is_refused_without_touching_the_secret(self, bad):
        with pytest.raises(TotpError):
            totp.verify(totp.new_secret(), bad)

    def test_two_accounts_never_share_a_secret(self):
        assert totp.new_secret() != totp.new_secret()

    def test_the_provisioning_uri_is_one_an_authenticator_can_read(self):
        uri = totp.provisioning_uri("JBSWY3DPEHPK3PXP", username="admin")
        assert uri.startswith("otpauth://totp/")
        assert "secret=JBSWY3DPEHPK3PXP" in uri and "period=30" in uri and "digits=6" in uri


# ------------------------------------------------------------------ enrolment


class TestEnrolment:
    def test_the_secret_is_not_stored_until_a_code_proves_the_app_works(self, auth):
        begin = auth.begin_enrolment("admin")
        assert begin["uri"].startswith("otpauth://")
        assert auth._users["admin"].totp_secret is None, "an unproved secret would lock the holder out"
        with pytest.raises(TotpError):
            auth.confirm_enrolment("admin", "000000")
        assert auth._users["admin"].totp_secret is None
        auth.confirm_enrolment("admin", totp.code_now(begin["secret"]))
        assert auth._users["admin"].totp_secret == begin["secret"]

    def test_confirming_without_starting_is_refused(self, auth):
        with pytest.raises(AuthError):
            auth.confirm_enrolment("admin", "123456")

    def test_a_pending_enrolment_is_never_written_to_disk(self, auth, tmp_path):
        auth.begin_enrolment("admin")
        assert AuthService(auth.dir, seed_default=False)._pending_enrolment == {}

    def test_the_enrolment_code_is_burned_so_it_cannot_also_sign_in(self, auth):
        begin = auth.begin_enrolment("admin")
        code = totp.code_now(begin["secret"])
        auth.confirm_enrolment("admin", code)
        with pytest.raises(TotpError):
            auth.authenticate_with_mfa("admin", "Admin#2026", code=code, required_roles=REQUIRED)


# ------------------------------------------------------------------ the two-step sign-in


class TestSignIn:
    def test_a_role_that_does_not_require_a_second_factor_signs_in_on_a_password(self, auth):
        p = auth.authenticate_with_mfa("user", "User#2026", required_roles=REQUIRED)
        assert p.token and p.mfa_satisfied

    def test_a_required_role_that_has_not_enrolled_is_let_in_and_flagged(self, auth):
        p = auth.authenticate_with_mfa("admin", "Admin#2026", required_roles=REQUIRED)
        assert p.token, "locking out an account that cannot yet enrol would be a denial of service"
        assert not p.mfa_enrolled and not p.mfa_satisfied, "the UI needs to know a factor is owed"

    def test_an_enrolled_account_cannot_sign_in_on_the_password_alone(self, enrolled):
        auth, _ = enrolled
        with pytest.raises(MfaRequired):
            auth.authenticate_with_mfa("admin", "Admin#2026", required_roles=REQUIRED)

    def test_the_half_finished_sign_in_leaves_no_usable_token(self, enrolled):
        auth, _ = enrolled
        before = len(auth._tokens)
        with pytest.raises(MfaRequired):
            auth.authenticate_with_mfa("admin", "Admin#2026", required_roles=REQUIRED)
        assert len(auth._tokens) == before, "a token minted mid-sign-in would be a way past the factor"

    def test_the_password_is_checked_first_so_mfa_is_not_an_account_oracle(self, enrolled):
        auth, _ = enrolled
        with pytest.raises(AuthError):
            auth.authenticate_with_mfa("admin", "wrong-password", required_roles=REQUIRED)
        with pytest.raises(AuthError):
            auth.authenticate_with_mfa("nobody", "wrong-password", required_roles=REQUIRED)

    def test_a_right_password_and_a_fresh_code_signs_in(self, enrolled):
        auth, secret = enrolled
        later = time.time() + totp.STEP_SECONDS
        p = auth.authenticate_with_mfa("admin", "Admin#2026", code=totp.code_now(secret, later),
                                       required_roles=REQUIRED, at=later)
        assert p.token and p.mfa_satisfied and p.mfa_enrolled

    def test_a_code_cannot_be_replayed(self, enrolled):
        auth, secret = enrolled
        later = time.time() + totp.STEP_SECONDS
        code = totp.code_now(secret, later)
        auth.authenticate_with_mfa("admin", "Admin#2026", code=code, required_roles=REQUIRED, at=later)
        with pytest.raises(TotpError):
            auth.authenticate_with_mfa("admin", "Admin#2026", code=code, required_roles=REQUIRED, at=later)

    def test_a_wrong_code_leaves_no_token_behind(self, enrolled):
        auth, _ = enrolled
        before = len(auth._tokens)
        with pytest.raises(TotpError):
            auth.authenticate_with_mfa("admin", "Admin#2026", code="123456", required_roles=REQUIRED)
        assert len(auth._tokens) == before

    def test_one_account_s_code_does_not_work_on_another(self, auth):
        for who in ("admin", "manager"):
            begin = auth.begin_enrolment(who)
            auth.confirm_enrolment(who, totp.code_now(begin["secret"]))
        later = time.time() + totp.STEP_SECONDS
        managers_code = totp.code_now(auth._users["manager"].totp_secret, later)
        with pytest.raises(TotpError):
            auth.authenticate_with_mfa("admin", "Admin#2026", code=managers_code, required_roles=REQUIRED, at=later)

    def test_the_burned_counter_list_stays_small(self, enrolled):
        auth, secret = enrolled
        auth._users["admin"].totp_used_counters = list(range(1, 500))
        later = time.time() + 5 * totp.STEP_SECONDS
        auth.verify_totp("admin", totp.code_now(secret, later), at=later)
        assert len(auth._users["admin"].totp_used_counters) <= 2 * totp.VALID_WINDOW + 1


# ------------------------------------------------------------------ administration


class TestAdministration:
    def test_an_unenrolled_account_cannot_be_code_verified(self, auth):
        with pytest.raises(AuthError):
            auth.verify_totp("user", "123456")

    def test_disabling_returns_the_account_to_password_only(self, enrolled):
        auth, _ = enrolled
        auth.disable_mfa("admin")
        p = auth.authenticate_with_mfa("admin", "Admin#2026", required_roles=REQUIRED)
        assert p.token and not p.mfa_enrolled

    def test_the_user_listing_reports_enrolment(self, enrolled):
        auth, _ = enrolled
        rows = {u["username"]: u for u in auth.users()}
        assert rows["admin"]["mfa_enrolled"] is True
        assert rows["user"]["mfa_enrolled"] is False

    def test_enrolment_survives_a_restart(self, enrolled):
        auth, secret = enrolled
        assert AuthService(auth.dir, seed_default=False)._users["admin"].totp_secret == secret
