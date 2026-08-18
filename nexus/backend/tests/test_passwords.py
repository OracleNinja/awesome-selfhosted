"""Password hashing and policy tests."""

from __future__ import annotations

import pytest

from nexus.core.config import Settings, load_settings
from nexus.core.passwords import (
    MIN_PASSWORD_LENGTH,
    PasswordPolicyError,
    PasswordService,
    generate_password,
    validate_password,
)
from nexus.core.tokens import constant_time_compare, generate_token, hash_token

GOOD_PASSWORD = "correct-horse-battery-staple"


@pytest.fixture
def cheap_settings() -> Settings:
    """Minimum Argon2 parameters: same code path, less waiting."""
    return load_settings(
        password_hash_time_cost=1,
        password_hash_memory_kib=8,
        password_hash_parallelism=1,
    )


@pytest.fixture
def passwords(cheap_settings: Settings) -> PasswordService:
    return PasswordService(cheap_settings)


class TestHashing:
    async def test_hash_is_not_the_password(self, passwords: PasswordService) -> None:
        digest = await passwords.hash(GOOD_PASSWORD)
        assert GOOD_PASSWORD not in digest

    async def test_hash_is_argon2id(self, passwords: PasswordService) -> None:
        digest = await passwords.hash(GOOD_PASSWORD)
        assert digest.startswith("$argon2id$")

    async def test_same_password_hashes_differently_each_time(
        self, passwords: PasswordService
    ) -> None:
        """Per-password salts. Without them, identical passwords produce
        identical hashes and one rainbow table breaks every account."""
        first = await passwords.hash(GOOD_PASSWORD)
        second = await passwords.hash(GOOD_PASSWORD)
        assert first != second

    async def test_both_hashes_still_verify(self, passwords: PasswordService) -> None:
        for _ in range(2):
            digest = await passwords.hash(GOOD_PASSWORD)
            assert await passwords.verify(digest, GOOD_PASSWORD) is True

    async def test_wrong_password_fails(self, passwords: PasswordService) -> None:
        digest = await passwords.hash(GOOD_PASSWORD)
        assert await passwords.verify(digest, "not-the-password-at-all") is False

    async def test_corrupt_hash_is_a_failure_not_a_crash(self, passwords: PasswordService) -> None:
        """A row written by something other than this code must not 500 the
        login endpoint."""
        assert await passwords.verify("$argon2id$garbage", GOOD_PASSWORD) is False
        assert await passwords.verify("", GOOD_PASSWORD) is False

    async def test_dummy_verification_does_not_raise(self, passwords: PasswordService) -> None:
        await passwords.verify_dummy()


class TestRehashing:
    async def test_hash_made_with_weaker_parameters_needs_rehash(self) -> None:
        """Cost parameters must be able to rise over time, and existing users
        must keep working — that is why the parameters are encoded in the hash."""
        weak = PasswordService(
            load_settings(
                password_hash_time_cost=1,
                password_hash_memory_kib=8,
                password_hash_parallelism=1,
            )
        )
        strong = PasswordService(
            load_settings(
                password_hash_time_cost=3,
                password_hash_memory_kib=64,
                password_hash_parallelism=1,
            )
        )
        old_hash = await weak.hash(GOOD_PASSWORD)

        assert strong.needs_rehash(old_hash) is True
        # The old hash still verifies, so nobody is locked out by the upgrade.
        assert await strong.verify(old_hash, GOOD_PASSWORD) is True

    async def test_current_parameters_do_not_need_rehash(self, passwords: PasswordService) -> None:
        assert passwords.needs_rehash(await passwords.hash(GOOD_PASSWORD)) is False


class TestPolicy:
    @pytest.mark.parametrize(
        "password",
        ["", "short", "a" * (MIN_PASSWORD_LENGTH - 1), "aaaaaaaaaaaaaaaa"],
    )
    def test_rejects_weak_passwords(self, password: str) -> None:
        with pytest.raises(PasswordPolicyError):
            validate_password(password)

    def test_rejects_known_breached_passwords(self) -> None:
        with pytest.raises(PasswordPolicyError):
            validate_password("passwordpassword")

    def test_rejects_absurdly_long_passwords(self) -> None:
        """Argon2's cost scales with input size, so an unbounded field is a
        denial-of-service vector."""
        with pytest.raises(PasswordPolicyError):
            validate_password("a" + "b" * 2000)

    def test_accepts_a_passphrase(self) -> None:
        validate_password(GOOD_PASSWORD)

    async def test_service_enforces_policy_on_hash(self, passwords: PasswordService) -> None:
        with pytest.raises(PasswordPolicyError):
            await passwords.hash("weak")


class TestGeneratedPasswords:
    def test_generated_passwords_pass_policy(self) -> None:
        for _ in range(20):
            validate_password(generate_password())

    def test_generated_passwords_are_unique(self) -> None:
        assert len({generate_password() for _ in range(50)}) == 50

    def test_excludes_ambiguous_characters(self) -> None:
        """These get read off a terminal and typed once; 1/l and 0/O are where
        that goes wrong."""
        for _ in range(20):
            assert not set(generate_password()) & set("l0O1I")


class TestTokens:
    def test_tokens_are_unique(self) -> None:
        assert len({generate_token() for _ in range(100)}) == 100

    def test_tokens_have_enough_entropy(self) -> None:
        # 32 random bytes, URL-safe base64: ~43 characters.
        assert len(generate_token()) >= 40

    def test_hash_is_stable_and_hex(self) -> None:
        token = generate_token()
        assert hash_token(token) == hash_token(token)
        assert len(hash_token(token)) == 64
        int(hash_token(token), 16)  # raises if not hex

    def test_hash_does_not_contain_the_token(self) -> None:
        token = generate_token()
        assert token not in hash_token(token)

    def test_constant_time_compare(self) -> None:
        assert constant_time_compare("abc", "abc") is True
        assert constant_time_compare("abc", "abd") is False
        assert constant_time_compare("abc", "") is False
