"""
This file originally came from
https://github.com/WujiangXu/A-mem-sys/blob/main/agentic_memory/memory_system.py
and was later modified.
"""

import json
from typing import Dict, List, Optional, Tuple

from wichy.console import user_console
from wichy.context.handler import ContextHandler
from wichy.helpers.document_store import DocumentStore
from wichy.llm_backend import call
from wichy.memory.zettelkasten.note import MemoryNote
from wichy.memory.zettelkasten.prompts import (
    ANALYZE_CONTENT_PROMPT,
    EVOLUTION_SYSTEM_PROMPT,
)
from wichy.memory.zettelkasten.tool_analyze_content_answer import (
    AnalyzedContentAnswerParameters,
    AnalyzedContentAnswerTool,
)
from wichy.memory.zettelkasten.tool_process_memory import (
    DoneTool,
    ModifyMemoryTool,
    ModifyNewMemoryParameters,
    ModifyNewMemoryTool,
)
from wichy.tools.helpers import get_tool_definitions


class AgenticMemorySystem:
    """Core memory system that manages memory notes and their evolution.

    This system provides:
    - Memory creation, retrieval, update, and deletion
    - Content analysis and metadata extraction
    - Memory evolution and relationship management
    - Search capabilities
    """

    def __init__(
        self,
        model_str: str,
        evo_threshold: int = 100,
    ):
        """Initialize the memory system.

        Args:
            model_str: The model string to use when calling llm backend.
            evo_threshold: Number of memories before triggering evolution.
        """
        self.memories: dict[str, MemoryNote] = {}
        self.model_str = model_str
        ds = DocumentStore(collection_name="memories")

        ds.clear()  # there is a possibility that a memory collection already exists

        self.ds = ds

        self.evo_cnt = 0
        self.evo_threshold = evo_threshold

    def add_note(self, note: MemoryNote):
        """Add a new memory note

        Args:
            note: The note to add.
        """

        if note.needs_analysis():
            analysis = self.analyze_content(note.content)

            # Only update attributes that are not provided or have default values
            if not note.keywords:
                note.keywords = analysis.get("keywords", [])
            if note.context == MemoryNote(content="").context:
                note.context = analysis.get("context", MemoryNote(content="").context)
            if not note.tags:
                note.tags = analysis.get("tags", [])

        # Update document store with all documents
        evo_label, note = self.process_memory(note)

        # Add to ChromaDB with complete metadata
        metadata = note.model_dump()
        id_in_store = self.ds.add_document(note.content, metadata)
        if note.id != id_in_store:
            user_console.print(
                "[yellow]Warning:[/yellow]: Memory note received a new id from store, that is unexpected but will work."
            )
            note.id = id_in_store

        self.memories[note.id] = note

        if evo_label == True:
            self.evo_cnt += 1
            if self.evo_cnt % self.evo_threshold == 0:
                self.consolidate_memories()
        return note.id

    def analyze_content(self, content: str) -> Dict:
        """Analyze content using LLM to extract semantic metadata.

        Uses a language model to understand the content and extract:
        - Keywords: Important terms and concepts
        - Context: Overall domain or theme
        - Tags: Classification categories

        Args:
            content (str): The text content to analyze

        Returns:
            Dict: Contains extracted metadata with keys:
                - keywords: List[str]
                - context: str
                - tags: List[str]
        """

        ctx = ContextHandler(sub_dir="memory", custom_suffix="analyze_content")

        ctx.add(role="system", content=ANALYZE_CONTENT_PROMPT)
        to_be_analyzed = "Content for analysis\n---\n\n" + content
        ctx.add(role="user", content=to_be_analyzed)

        c = 0

        answer_tool = AnalyzedContentAnswerTool()

        tool_defs = get_tool_definitions([answer_tool])

        while True:
            if c > 3:
                # model doesn't seem to be able to do this, we need to quit.
                return {
                    "keywords": [],
                    "context": MemoryNote(content="").context,
                    "tags": [],
                }

            response = call(
                context=ctx(), tool_defs=tool_defs, model_str=self.model_str
            )
            ctx.append(
                {
                    "role": "assistant",
                    "content": response.message.content,
                    "tool_calls": [
                        t.model_dump() for t in (response.message.tool_calls or [])
                    ],
                }
            )

            if response.message.finish_reason != "tool_calls":
                if c == 0:
                    ctx.add(
                        role="user",
                        content="your must answer using tool "
                        + answer_tool.name
                        + ". Try again.",
                    )
                c += 1
                continue  # let us try again

            # we have a tool call!
            if not response.message.tool_calls:
                # but doesn't seem to be any tool call around...
                c += 1
                continue

            # at least one tool call
            item = response.message.tool_calls[0]
            name = item.function.name
            if name != answer_tool.name:
                # called tool, but wrong name
                ctx.append(
                    {
                        "role": "tool",
                        "tool_call_id": item.id,
                        "content": "no tool named "
                        + name
                        + ", use "
                        + answer_tool.name,
                    }
                )
                c += 1
                continue

            # called tool and name is matching
            args = json.loads(item.function.arguments)

            # We will use this method just to validate the params and later extract them.
            result = answer_tool.validate_and_execute(**args)
            if result.startswith("error"):
                ctx.append({"role": "tool", "tool_call_id": item.id, "content": result})
                c += 1
                continue

            # reaching here means that we should have proper parmeters in args, these we want to return
            xs: AnalyzedContentAnswerParameters = answer_tool.parameters_model(**args)
            return {"keywords": xs.keywords, "context": xs.context, "tags": xs.tags}

    def consolidate_memories(self):
        """Consolidate memories: update retriever with new documents"""
        # Reset ChromaDB collection
        self.ds.clear()

        # Re-add all memory documents with their complete metadata
        for memory in self.memories.values():
            metadata = memory.model_dump()
            id_in_store = self.ds.add_document(memory.content, metadata)

    def find_related_memories(self, query: str, k: int = 5) -> List[MemoryNote]:
        """Find related memories using DocumentStore

        Returns:
            List[MemoryNote]: List of memory notes
        """
        if not self.memories:
            return []

        results = self.ds.search(query, k)
        mns: List[MemoryNote] = []

        if (
            "ids" in results
            and results["ids"]
            and len(results["ids"]) > 0
            and len(results["ids"][0]) > 0
        ):
            for i, _ in enumerate(results["ids"][0]):
                # Get metadata from ChromaDB results
                if i < len(results["metadatas"][0]):
                    metadata = results["metadatas"][0][i]
                    # TODO maybe I should just extract the ids and then fetch from the self.memories here. idk.
                    m = MemoryNote(**metadata)
                    mns.append(m)

        return mns

    def read(self, memory_id: str) -> Optional[MemoryNote]:
        """Retrieve a memory note by its ID.

        Args:
            memory_id (str): ID of the memory to retrieve

        Returns:
            MemoryNote if found, None otherwise
        """
        return self.memories.get(memory_id)

    def update(self, new_version_of_note: MemoryNote) -> bool:
        """Update a memory note.

        Args:
            memory_id: ID of memory to update
            **kwargs: Fields to update

        Returns:
            bool: True if update successful
        """
        nid = new_version_of_note.id
        if nid not in self.memories:
            return False

        self.memories[nid] = new_version_of_note

        metadata = new_version_of_note.model_dump()

        self.ds.update_document(nid, new_version_of_note.content, metadata)

        return True

    def delete(self, memory_id: str) -> bool:
        """Delete a memory note by its ID.

        Args:
            memory_id (str): ID of the memory to delete

        Returns:
            bool: True if memory was deleted, False if not found
        """
        if memory_id in self.memories:
            # Delete from ChromaDB
            self.ds.delete_document(memory_id)
            # Delete from local storage
            del self.memories[memory_id]
            return True
        return False

    def search(self, query: str, k: int = 5) -> List[MemoryNote]:
        """Search for memories using semantic similarity"""
        # Get results from ChromaDB (only do this once)
        search_results = self.ds.search(query, k)
        memories = []

        # Process ChromaDB results
        for i, doc_id in enumerate(search_results["ids"][0]):
            memory = self.memories.get(doc_id)
            if memory == None:
                continue
            memories.append(memory)

        return memories[:k]

    def process_memory(self, note: MemoryNote) -> Tuple[bool, MemoryNote]:
        """Process a memory note and determine if it should evolve.

        Args:
            note: The memory note to process

        Returns:
            Tuple[bool, MemoryNote]: (should_evolve, processed_note)
        """
        # For first memory or testing, just return the note without evolution
        if not self.memories:
            return False, note

        memory_notes = self.find_related_memories(note.content, k=5)
        if len(memory_notes) < 1:
            return False, note

        # Format neighbors for LLM
        neighbors_text = ""
        for n in memory_notes:
            neighbors_text += "\n---\n" + n.to_memory_string()

        neighbors_text += "\n---\n"

        # Query LLM for evolution decision
        prompt = EVOLUTION_SYSTEM_PROMPT.format(
            content=note.content,
            context=note.context,
            keywords=note.keywords,
            nearest_neighbors_memories=neighbors_text,
        )

        ctx = ContextHandler(custom_suffix="process_memory", sub_dir="memory")
        ctx.add(role="system", content=prompt)
        ctx.add(role="user", content="Start processing")

        tools = [
            ModifyNewMemoryTool(),
            ModifyMemoryTool(),
            DoneTool(),
        ]
        tool_defs = get_tool_definitions(tools=tools)

        c = 0
        has_evolved = False
        has_error = False
        while True:
            if c > 10:  # shouldn't get here
                return (has_evolved, note)

            response = call(
                context=ctx(), tool_defs=tool_defs, model_str=self.model_str
            )
            ctx.append(
                {
                    "role": "assistant",
                    "content": response.message.content,
                    "tool_calls": [
                        t.model_dump() for t in (response.message.tool_calls or [])
                    ],
                }
            )

            if response.message.finish_reason != "tool_calls":
                if c < 1:
                    ctx.add(
                        role="user",
                        content="use tool calls " + ",".join([x.name for x in tools]),
                    )
                c += 1
                continue

            # we have a tool call!
            has_error = False
            for tc in response.message.tool_calls:
                item = tc
                name = item.function.name

                t = None

                for y in tools:
                    if y.name == name:
                        t = y
                        break

                if not t:
                    # called tool, but wrong name
                    ctx.append(
                        {
                            "role": "tool",
                            "tool_call_id": item.id,
                            "content": "no tool named " + name,
                        }
                    )
                    continue

                # called tool and name is matching
                args = json.loads(item.function.arguments)
                args["memory_note"] = note
                args["memory_notes"] = (
                    memory_notes  # NB: This list of notes won't be updated between calls.
                )

                result = t.validate_and_execute(**args)
                if result.startswith("error"):
                    ctx.append(
                        {"role": "tool", "tool_call_id": item.id, "content": result}
                    )
                    has_error = True
                    continue

                if not has_error and result == DoneTool().exit_value:
                    ctx.append(
                        {
                            "role": "tool",
                            "tool_call_id": item.id,
                            "content": "now exiting...",
                        }
                    )
                    return (has_evolved, note)

                if t.name == ModifyNewMemoryTool().name:
                    x = MemoryNote.model_validate_json(result)
                    note = x
                    # need to see if it was evolved
                    p: ModifyNewMemoryParameters = t.parameters_model(**args)
                    has_evolved = p.should_evolve
                    ctx.append(
                        {
                            "role": "tool",
                            "tool_call_id": item.id,
                            "content": "modification successful",
                        }
                    )
                    continue
                if t.name == ModifyMemoryTool().name:
                    x = MemoryNote.model_validate_json(result)
                    self.update(x)
                    # the fact that we reach this part of the code is proof that somethings has changed
                    has_evolved = True
                    ctx.append(
                        {
                            "role": "tool",
                            "tool_call_id": item.id,
                            "content": "modification successful",
                        }
                    )
                    continue
