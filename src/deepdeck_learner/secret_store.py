from __future__ import annotations

import os
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

try:
    import keyring
except ImportError:  # pragma: no cover - exercised only in minimal installations
    keyring = None  # type: ignore[assignment]


SERVICE_NAME = "DeepDeckLearner"
ACCOUNT_NAME = "deepdeck-api-key"
RUNTIME_SOURCE = "DEEPDECK_LEARNER_API_KEY_SOURCE"


@dataclass(frozen=True)
class SecretStatus:
    configured: bool
    provider: str | None
    externally_managed: bool


def validate_api_key(value: str) -> str:
    normalized = value.strip()
    if (
        not normalized.startswith("ddl_agent_")
        or len(normalized) < 20
        or len(normalized) > 512
        or any(character.isspace() for character in normalized)
    ):
        raise ValueError("Enter a complete Deep Deck League key beginning with ddl_agent_.")
    return normalized


class AccountSecretStore:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    @property
    def dotenv_path(self) -> Path:
        return self.root / ".env"

    def _system_secret(self) -> str | None:
        if keyring is None:
            return None
        try:
            return keyring.get_password(SERVICE_NAME, ACCOUNT_NAME)
        except Exception:
            return None

    def _dotenv_secret(self) -> str | None:
        if not self.dotenv_path.is_file():
            return None
        for line in self.dotenv_path.read_text("utf-8").splitlines():
            name, separator, value = line.partition("=")
            if separator and name.strip() == "DEEPDECK_API_KEY":
                return value.strip().strip("\"'") or None
        return None

    def load_into_environment(self) -> SecretStatus:
        current = os.getenv("DEEPDECK_API_KEY", "").strip()
        source = os.getenv(RUNTIME_SOURCE)
        if current and not source:
            dotenv = self._dotenv_secret()
            if dotenv and current == dotenv:
                os.environ[RUNTIME_SOURCE] = "dotenv"
                return SecretStatus(True, "dotenv", False)
            return SecretStatus(True, "environment", True)
        system = self._system_secret()
        if system:
            os.environ["DEEPDECK_API_KEY"] = system
            os.environ[RUNTIME_SOURCE] = "system"
            return SecretStatus(True, "system", False)
        dotenv = self._dotenv_secret()
        if dotenv:
            os.environ["DEEPDECK_API_KEY"] = dotenv
            os.environ[RUNTIME_SOURCE] = "dotenv"
            return SecretStatus(True, "dotenv", False)
        os.environ.pop(RUNTIME_SOURCE, None)
        return SecretStatus(False, None, False)

    def status(self) -> SecretStatus:
        current = os.getenv("DEEPDECK_API_KEY", "").strip()
        source = os.getenv(RUNTIME_SOURCE)
        if current:
            return SecretStatus(True, source or "environment", source is None)
        return self.load_into_environment()

    def save(self, value: str) -> SecretStatus:
        secret = validate_api_key(value)
        current = self.status()
        if current.externally_managed:
            raise RuntimeError(
                "DEEPDECK_API_KEY is managed by the process environment. Remove it there "
                "before replacing it in the workbench."
            )
        if keyring is None:
            raise RuntimeError(
                "The operating-system credential vault is unavailable. "
                "DeepDeckLearner did not write the key to .env."
            )
        try:
            keyring.set_password(SERVICE_NAME, ACCOUNT_NAME, secret)
        except Exception as error:
            raise RuntimeError(
                "The operating-system credential vault rejected the key. "
                "DeepDeckLearner did not write it to .env."
            ) from error
        self._remove_dotenv_secret()
        os.environ["DEEPDECK_API_KEY"] = secret
        os.environ[RUNTIME_SOURCE] = "system"
        return SecretStatus(True, "system", False)

    def delete(self) -> SecretStatus:
        current = self.status()
        if current.externally_managed:
            raise RuntimeError(
                "This key is managed by the process environment and cannot be removed here."
            )
        if keyring is not None:
            with suppress(Exception):
                keyring.delete_password(SERVICE_NAME, ACCOUNT_NAME)
        self._remove_dotenv_secret()
        os.environ.pop("DEEPDECK_API_KEY", None)
        os.environ.pop(RUNTIME_SOURCE, None)
        return SecretStatus(False, None, False)

    def _remove_dotenv_secret(self) -> None:
        if not self.dotenv_path.is_file():
            return
        lines = self.dotenv_path.read_text("utf-8").splitlines()
        filtered = [line for line in lines if line.partition("=")[0].strip() != "DEEPDECK_API_KEY"]
        if filtered:
            self.dotenv_path.write_text("\n".join(filtered).rstrip() + "\n", "utf-8")
            os.chmod(self.dotenv_path, 0o600)
        else:
            self.dotenv_path.unlink()
