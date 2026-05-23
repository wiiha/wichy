from typing import Dict, List, Optional

from prompt_toolkit.shortcuts import checkboxlist_dialog, input_dialog, radiolist_dialog
from prompt_toolkit.styles import Style

from wichy.console import user_console
from wichy.helpers.needs_user_attention import needs_user_attention
from wichy.tools.ask_user_question import Question, QuestionOption
from wichy.tools.human_verification import _user_interaction_lock
from wichy.helpers.interaction_provider import InteractionProvider


class REPLInteractionProvider(InteractionProvider):
    def ask_questions(
        self, questions: List[Question], metadata: Optional[Dict] = None
    ) -> Dict:
        answers: Dict[str, str] = {}

        style = Style.from_dict(
            {
                "dialog": "bg:#000000 #ffffff",
                "dialog.frame": "bg:#000000 #ffffff",
                "dialog.body": "bg:#000000 #ffffff",
                "dialog.title": "bg:#000000 #ffffff bold",
            }
        )

        with _user_interaction_lock:
            with user_console.paused():
                needs_user_attention()
                for question in questions:
                    header = question.header
                    if header is None:
                        continue

                    values = [
                        (
                            opt.label if isinstance(opt, QuestionOption) else str(opt),
                            f"{opt.label if isinstance(opt, QuestionOption) else str(opt)}: {opt.description if isinstance(opt, QuestionOption) else str(opt)}",
                        )
                        for opt in question.options
                    ]

                    other_label = "Other (please specify)"
                    other_was_passed = any(
                        (opt.label if isinstance(opt, QuestionOption) else str(opt))
                        .lower()
                        .strip()
                        == "other"
                        for opt in question.options
                    )
                    if not other_was_passed:
                        values.append((other_label, "Provide a custom answer"))

                    title = "Question"
                    if metadata and "source" in metadata:
                        title = f"Question from {metadata['source']}"

                    if question.multiSelect:
                        multi_result = checkboxlist_dialog(
                            title=title,
                            text=question.question,
                            values=values,
                            style=style,
                        ).run()
                        filtered = [r for r in (multi_result or []) if r != other_label]
                        answers[header] = (
                            ", ".join(filtered) if filtered else "No selection"
                        )
                    else:
                        single_result = radiolist_dialog(
                            title=title,
                            text=question.question,
                            values=values,
                            style=style,
                        ).run()

                        if single_result and single_result != other_label:
                            answers[header] = single_result
                        elif single_result == other_label:
                            custom = input_dialog(
                                title="Custom Answer",
                                text=f"You selected 'Other' for '{question.question}'. Please specify:",
                                style=style,
                            ).run()
                            answers[header] = custom or "No selection"
                        else:
                            answers[header] = "No selection"

        output = {"answers": answers}
        if metadata:
            output["metadata"] = metadata
        return output
