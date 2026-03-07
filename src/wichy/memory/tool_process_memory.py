from typing import List, Literal, Optional

from pydantic import BaseModel, Field

from wichy.helpers.string import truncate_to_len
from wichy.memory.note import MemoryNote
from wichy.tools.base import BaseTool, ParametersModel


class ModifyNewMemoryParameters(ParametersModel):
    should_evolve: bool = Field(..., description="Does the new note need any updates?")
    tags: Optional[list[str]] = Field(
        [],
        description=(
            "The full set of new tags, should also include old tags that should be kept."
        ),
    )
    links_to_neighbor_memories: Optional[list[str]] = Field(
        [],
        description="List of ids that this new memory note should connect to. "
        "You can select from the once that have been presented to you. Always present the full list",
    )
    memory_note: MemoryNote = Field(
        Optional[MemoryNote],
        description="HIDE_FROM_LLM The new note that will be modified and then returned using json string serialization.",
    )

    def info(self):

        return f'tags="{self.tags}", new_links_to_neighbor_memories="{self.links_to_neighbor_memories}"'


class ModifyNewMemoryTool(BaseTool):
    name = "modify_new_memory"
    description = "Modify the new memory."
    description_long = """Modify the new memory.
You are expected to call this tool, even if it is to just indicate that no evolution is needed."""

    parameters_model = ModifyNewMemoryParameters

    def execute(
        self,
        should_evolve: bool,
        tags: list[str],
        links_to_neighbor_memories: list[str],
        memory_note: MemoryNote,
    ) -> str:
        memory_note = MemoryNote(**memory_note)
        if not should_evolve:
            return memory_note.model_dump_json()

        if tags:
            memory_note.tags = tags
        if links_to_neighbor_memories:
            memory_note.links = links_to_neighbor_memories

        return memory_note.model_dump_json()


class ModifyNeighborMemoryParameters(ParametersModel):
    memory_id: str = (
        Field(
            ...,
            description=("The memory_id for which these changes are relevant."),
        ),
    )
    context: str = Field(
        "",
        description=(
            "The full new version of the context string that you see fit based on the available notes."
        ),
    )
    tags: Optional[list[str]] = Field(
        [],
        description=(
            "The full set of new tags, should also include old tags that should be kept."
        ),
    )
    memory_notes: Optional[list[MemoryNote]] = Field(
        ...,
        description="HIDE_FROM_LLM The new note that will be modified and then returned using json string serialization.",
    )

    def info(self):

        return f'memory_id="{self.memory_id}" context="{truncate_to_len(self.context)}", tags="{self.tags}"'


class ModifyMemoryTool(BaseTool):
    name = "modify_neighbor_memory"
    description = "Modify a memory by id."
    description_long = """
Modify a memory by id.
You call this tool for every neighbor memory that you want to modify.
"""

    parameters_model = ModifyNeighborMemoryParameters

    def execute(
        self,
        memory_id: str,
        context: str,
        tags: list[str],
        memory_notes: list[MemoryNote],
    ) -> str:
        n: Optional[MemoryNote] = None
        xx = [MemoryNote(**x) for x in memory_notes]
        memory_notes = xx
        for x in memory_notes:
            if x.id == memory_id:
                n = x
                break

        if n == None:
            raise ValueError("no neighbor memory has id " + memory_id)

        if context != "":
            n.context = context
        if tags:
            n.tags = tags

        return n.model_dump_json()


class DoneProcessingParameters(ParametersModel):

    def info(self):

        return f'"LLM feels done processing memories."'


class DoneTool(BaseTool):
    name = "exit_processing"
    description = "Call this tool as your last action."
    description_long = (
        "Call this tool as your last action, when you feel done updating memories."
    )
    exit_value = "EXIT"

    parameters_model = DoneProcessingParameters

    def execute(self) -> str:
        return self.exit_value
