#!/usr/bin/env python3
"""Script to try out the AgenticMemorySystem functionality."""

from wichy.memory.memory import AgenticMemorySystem
from wichy.memory.note import MemoryNote

def print_separator(title=""):
    """Print a separator line with optional title."""
    if title:
        print(f"\n{'='*60}")
        print(f"{title:^60}")
        print(f"{'='*60}\n")
    else:
        print(f"\n{'-'*60}\n")

def demo_basic_operations(memory):
    """Demonstrate basic CRUD operations."""
    print_separator("1. Basic CRUD Operations")
    
    # Create a simple memory note
    print("Creating a simple memory note...")
    note1 = MemoryNote(content="User prefers Python over JavaScript for data science tasks.")
    note_id1 = memory.add_note(note1)
    print(f"✓ Created note with ID: {note_id1}")
    
    # Create a note with pre-filled metadata
    print("\nCreating a note with pre-filled metadata...")
    note2 = MemoryNote(
        content="The meeting is scheduled for Friday at 3pm in room 302.",
        keywords=["meeting", "schedule", "Friday"],
        context="Work",
        tags=["calendar", "planning"]
    )
    note_id2 = memory.add_note(note2)
    print(f"✓ Created note with ID: {note_id2}")
    
    # Read a memory
    print("\nReading memory by ID...")
    retrieved_note = memory.read(note_id1)
    if retrieved_note:
        print(f"✓ Retrieved: {retrieved_note.content}")
    
    # Update a memory
    print("\nUpdating a memory...")
    retrieved_note.keywords.append("programming")
    success = memory.update(retrieved_note)
    print(f"✓ Update {'successful' if success else 'failed'}")
    
    # Show all memories
    print("\nCurrent memories in system:")
    for mid, mnote in memory.memories.items():
        print(f"  - [{mid[:8]}...] {mnote.content[:50]}...")
    
    return note_id1, note_id2

def demo_search(memory):
    """Demonstrate search and retrieval capabilities."""
    print_separator("2. Search and Retrieval")
    
    # Search for memories
    queries = [
        "programming languages",
        "meetings",
        "schedule",
    ]
    
    for query in queries:
        print(f"\nSearching for: '{query}'")
        results = memory.search(query, k=3)
        print(f"  Found {len(results)} result(s):")
        for i, result in enumerate(results, 1):
            print(f"    {i}. {result.content[:70]}...")
            print(f"      Context: {result.context}, Tags: {result.tags}")

def demo_find_related(memory):
    """Demonstrate finding related memories."""
    print_separator("3. Finding Related Memories")
    
    # Add a third note to test relationships
    print("Adding a third memory to test relationships...")
    note3 = MemoryNote(
        content="Python is great for machine learning with libraries like TensorFlow."
    )
    note_id3 = memory.add_note(note3)
    print(f"✓ Created note with ID: {note_id3}")
    
    # Find memories related to a specific content
    query_content = "JavaScript is used for web development."
    print(f"\nFinding memories related to: '{query_content}'")
    related = memory.find_related_memories(query_content, k=3)
    print(f"  Found {len(related)} related memory(ies):")
    for i, rel in enumerate(related, 1):
        print(f"    {i}. {rel.content[:70]}...")
        print(f"      Keywords: {rel.keywords}")

def demo_delete(memory, note_id1, note_id2):
    """Demonstrate memory deletion."""
    print_separator("4. Deleting Memories")
    
    # Delete a memory
    print(f"Deleting memory with ID: {note_id1}")
    success = memory.delete(note_id1)
    print(f"  Delete {'successful' if success else 'failed'}")
    
    print(f"\nRemaining memories: {len(memory.memories)}")
    for mid in memory.memories:
        print(f"  - {mid[:8]}...")

def demo_consolidation(memory):
    """Demonstrate memory consolidation."""
    print_separator("5. Memory Consolidation")
    
    print(f"Current evolution count: {memory.evo_cnt}")
    print(f"Evolution threshold: {memory.evo_threshold}")
    
    # Manually trigger consolidation
    print("\nManually triggering consolidation...")
    memory.consolidate_memories()
    print("✓ Consolidation complete. Document store updated.")

def main():
    """Main function to run all demos."""
    print("\n" + "="*60)
    print(" AGENTIC MEMORY SYSTEM DEMO")
    print("="*60)
    
    # Initialize the memory system with the specified model
    model_str = "open_router/stepfun/step-3.5-flash:free"
    print(f"\nInitializing memory system with model: {model_str}")
    
    memory = AgenticMemorySystem(
        model_str=model_str,
        evo_threshold=2  # Set low for demo purposes
    )
    print("✓ Memory system initialized")
    
    try:
        # Run demos
        note_id1, note_id2 = demo_basic_operations(memory)
        demo_search(memory)
        demo_find_related(memory)
        demo_delete(memory, note_id1, note_id2)
        demo_consolidation(memory)
        
        print_separator("Demo Complete")
        print("All memory system functionalities have been demonstrated.")
        print("Check the output above to see how each feature works.")
        
    except Exception as e:
        print(f"\n❌ Error during demo: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()
