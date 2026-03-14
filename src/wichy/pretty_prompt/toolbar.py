from prompt_toolkit import prompt
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.styles import Style


class BottomToolbar:
    def __init__(self):
        self.content = '<style bg="black">WELCOME!</style>'

    def update(self, new_content):
        self.content = new_content

    def render(self):
        out = self.content
        return HTML(out)

    def style(self):
        return Style.from_dict(
            {
                "bottom-toolbar": "#fff",
            }
        )

        return o
