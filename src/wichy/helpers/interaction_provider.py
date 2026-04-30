from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional


class InteractionProvider(ABC):
    @abstractmethod
    def ask_questions(
        self, questions: List[Any], metadata: Optional[Dict] = None
    ) -> Dict:
        """
        Render questions to the user and block until answers arrive.
        Returns: {"answers": {header: answer, ...}, "metadata": {...}}
        """


_interaction_provider: Optional[InteractionProvider] = None


def set_interaction_provider(p: InteractionProvider) -> None:
    global _interaction_provider
    _interaction_provider = p


def get_interaction_provider() -> Optional[InteractionProvider]:
    return _interaction_provider
