import json
import os
import re
import uuid
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field

from wichy.tools.base import BaseTool, ParametersModel

# --- Module-level helpers ---


def get_graphs_dir() -> str:
    """Get the graphs directory, creating it if needed."""
    workspace = os.getcwd()
    graphs_dir = os.path.join(workspace, ".wichy", "graphs")
    os.makedirs(graphs_dir, exist_ok=True)
    return graphs_dir


# --- Tool classes ---


class CreateGraphParameters(ParametersModel):
    content: str = Field(
        ...,
        description="Graph definition in simple text format. Use '## Nodes:' section with 'Label [#color]' per line, and '## Edges:' section with 'From -> To [label]' per line. Colors are optional hex codes like #FF0000.",
    )

    def info(self):
        lines = self.content.strip().split("\n")
        return f"{len(lines)} lines"


class CreateGraphTool(BaseTool):
    name = "create_graph"
    description = "Create a new graph from text definition"
    description_long = """Create a graph from a simple text format and save it to .wichy/graphs/.

Format example:
## Nodes:
  Alice [#FF6B6B]
  Bob [#4ECDC4]
  Carol

## Edges:
  Alice -> Bob [knows]
  Bob -> Carol

Node colors are optional (defaults to blue). Edge labels are optional and should mainly use alphanumeric characters, underscores and hyphens (e.g., works_with, related-to). The graph will be saved and can be loaded in the graph editor."""
    parameters_model = CreateGraphParameters

    def _generate_id(self):
        """Generate a unique node ID."""
        return str(uuid.uuid4())[:8]

    def _parse_color(self, color_str: Optional[str]) -> str:
        """Parse and validate color string."""
        if not color_str:
            return "#97C2FC"
        # Clean up and validate hex color
        color_str = color_str.strip()
        if color_str.startswith("#"):
            color_str = color_str[1:]
        if len(color_str) == 6 and all(
            c in "0123456789ABCDEFabcdef" for c in color_str
        ):
            return "#" + color_str
        return "#97C2FC"

    def execute(self, content: str) -> str:
        """Create a graph from text definition."""
        try:
            nodes = []
            edges = []
            node_label_to_id = {}

            lines = content.strip().split("\n")
            current_section = None

            for line in lines:
                line = line.strip()
                if not line:
                    continue

                # Check for section headers
                if line.startswith("## Nodes:"):
                    current_section = "nodes"
                    continue
                elif line.startswith("## Edges:"):
                    current_section = "edges"
                    continue

                # Parse nodes
                if current_section == "nodes":
                    # Format: Label [#color] or just Label
                    match = re.match(r"^(.+?)\s*(\[#[A-Fa-f0-9]{6}\])?\s*$", line)
                    if match:
                        label = match.group(1).strip()
                        color = "#97C2FC"
                        if match.group(2):
                            color = match.group(2).strip("[]")

                        node_id = self._generate_id()
                        node_label_to_id[label] = node_id
                        nodes.append(
                            {
                                "id": node_id,
                                "label": label,
                                "color": color,
                                "shape": "dot",
                                "size": 25,
                            }
                        )

                # Parse edges
                elif current_section == "edges":
                    # Format: From -> To [label]
                    match = re.match(r"^(.+?)\s*->\s*(.+?)(?:\s*\[(.+?)\])?\s*$", line)
                    if match:
                        from_label = match.group(1).strip()
                        to_label = match.group(2).strip()
                        edge_label = match.group(3).strip() if match.group(3) else ""

                        from_id = node_label_to_id.get(from_label)
                        to_id = node_label_to_id.get(to_label)

                        if from_id and to_id:
                            edge = {
                                "id": self._generate_id(),
                                "from": from_id,
                                "to": to_id,
                                "arrows": "to",
                            }
                            if edge_label:
                                edge["label"] = edge_label
                            edges.append(edge)
                        elif not from_id:
                            return f"error: Node '{from_label}' not found. Make sure to define all nodes in the ## Nodes: section first."
                        elif not to_id:
                            return f"error: Node '{to_label}' not found. Make sure to define all nodes in the ## Nodes: section first."

            if not nodes:
                return (
                    "error: No nodes defined. Use '## Nodes:' section with node labels."
                )

            # Create graph data
            graph_data = {"nodes": nodes, "edges": edges}

            # Save to file
            graphs_dir = get_graphs_dir()
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"graph_{timestamp}.json"
            filepath = os.path.join(graphs_dir, filename)

            with open(filepath, "w") as f:
                json.dump(graph_data, f, indent=2)

            # Also update latest.json
            latest_path = os.path.join(graphs_dir, "latest.json")
            with open(latest_path, "w") as f:
                json.dump(graph_data, f, indent=2)

            return f"Created graph with {len(nodes)} nodes and {len(edges)} edges.\nSaved to: {filename}\n\nYou can view it in the graph editor by refreshing the dropdown and selecting '{filename}' or 'latest.json'."

        except Exception as e:
            return f"error: {e}"


class ReadGraphParameters(ParametersModel):
    filename: Optional[str] = Field(
        "latest.json",
        description="graph filename to read (default: latest.json). Can also use a specific name like graph_20260311_184900.json",
    )

    def info(self):
        return f'filename="{self.filename}"'


class ReadGraphTool(BaseTool):
    name = "read_graph"
    description = "Read a saved graph JSON file"
    description_long = "Read a graph from .wichy/graphs/ and return its contents as a concise edge list format. Useful for agent to analyze graph structure."
    parameters_model = ReadGraphParameters

    def execute(self, filename: str = "latest.json") -> str:
        """Read a graph file."""
        try:
            graphs_dir = get_graphs_dir()
            filepath = os.path.join(graphs_dir, filename)

            if not os.path.exists(filepath):
                # List available files to help user
                files = self._list_files()
                if files:
                    file_list = "\n".join(f"  - {f}" for f in files[:10])
                    return (
                        f"File '{filename}' not found. Available graphs:\n{file_list}"
                    )
                else:
                    return f"File '{filename}' not found. No graphs saved yet."

            with open(filepath, "r") as f:
                data = json.load(f)

            # Format as concise edge list
            return self._format_as_edge_list(data, filename)

        except json.JSONDecodeError as e:
            return f"error: Invalid JSON in {filename}: {e}"
        except Exception as e:
            return f"error: {e}"

    def _format_as_edge_list(self, data: dict, filename: str) -> str:
        """Format graph data as a concise edge list."""
        lines = [f"# Graph: {filename}"]

        nodes = data.get("nodes", [])
        edges = data.get("edges", [])

        # Build node lookup
        node_map = {n["id"]: n for n in nodes}

        # Node summary
        lines.append(f"# {len(nodes)} nodes, {len(edges)} edges")
        lines.append("")

        # List nodes with their colors
        if nodes:
            lines.append("## Nodes:")
            for node in nodes:
                label = node.get("label", node["id"])
                color = node.get("color", "#97C2FC")
                if isinstance(color, dict):
                    color = color.get("background", "#97C2FC")
                lines.append(f"  {label} [{color}]")
            lines.append("")

        # List edges
        if edges:
            lines.append("## Edges:")
            for edge in edges:
                from_id = edge.get("from")
                to_id = edge.get("to")
                label = edge.get("label", "")

                from_node = node_map.get(from_id, {})
                to_node = node_map.get(to_id, {})

                from_label = from_node.get("label", from_id)
                to_label = to_node.get("label", to_id)

                if label:
                    lines.append(f"  {from_label} -> {to_label} [{label}]")
                else:
                    lines.append(f"  {from_label} -> {to_label}")

        return "\n".join(lines)

    def _list_files(self) -> List[str]:
        """List all graph files."""
        graphs_dir = get_graphs_dir()
        if not os.path.exists(graphs_dir):
            return []
        files = [
            f
            for f in os.listdir(graphs_dir)
            if f.endswith(".json") and os.path.isfile(os.path.join(graphs_dir, f))
        ]
        return sorted(files, reverse=True)


class ListGraphsParameters(ParametersModel):
    pass

    def info(self):
        return ""


class ListGraphsTool(BaseTool):
    name = "list_graphs"
    description = "List all saved graph files"
    description_long = (
        "List all graph JSON files in .wichy/graphs/ with file sizes and dates."
    )
    parameters_model = ListGraphsParameters

    def execute(self) -> str:
        """List all graph files."""
        try:
            graphs_dir = get_graphs_dir()

            if not os.path.exists(graphs_dir):
                return "No graphs directory found. No graphs saved yet."

            files = [
                f
                for f in os.listdir(graphs_dir)
                if f.endswith(".json") and os.path.isfile(os.path.join(graphs_dir, f))
            ]

            if not files:
                return "No graph files found in .wichy/graphs/."

            # Build detailed listing
            lines = []
            for f in sorted(files, reverse=True):
                filepath = os.path.join(graphs_dir, f)
                stat = os.stat(filepath)
                size_kb = stat.st_size / 1024
                from datetime import datetime

                mtime = datetime.fromtimestamp(stat.st_mtime).strftime(
                    "%Y-%m-%d %H:%M:%S"
                )
                lines.append(f"{f:<30} {size_kb:>8.1f} KB  {mtime}")

            result = f"Found {len(files)} graph file(s):\n"
            result += "\n".join(lines)
            return result

        except Exception as e:
            return f"error: {e}"
