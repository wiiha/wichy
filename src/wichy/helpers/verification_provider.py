from abc import ABC, abstractmethod
from typing import Any, Callable, Optional

from pydantic import BaseModel


class VerificationResponse(BaseModel):
    ok: bool = False
    reason: str = ""


class VerificationProvider(ABC):

    @abstractmethod
    def verify(self, label: str, message: str, all_args: str) -> VerificationResponse:
        """Method that provider has to be implemented for every provider."""
        pass


verification_provider: Optional[VerificationProvider] = None


def get_verification_provider():
    return external_verification_provider


def set_verification_provider(
    provider: VerificationProvider,
) -> None:
    global external_verification_provider
    external_verification_provider = provider
