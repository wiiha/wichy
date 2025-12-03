from pydantic import BaseModel, Field
from typing import Optional
import os
import pathspec
from .base import BaseTool


class TreeParameters(BaseModel):
    path: Optional[str] = Field(
        ".",
        description="directory to generate tree for, default=current directory",
    )
    max_depth: Optional[int] = Field(
        None,
        description="maximum depth to traverse, default=unlimited",
    )


class TreeTool(BaseTool):
    name = "tree"
    description = "Display directory tree structure, respecting .gitignore files"
    parameters_model = TreeParameters

    def _load_gitignore(self, root_path: str) -> pathspec.PathSpec:
        """Load .gitignore patterns."""
        patterns = ['.git/']  # Always ignore .git
        
        gitignore_path = os.path.join(root_path, '.gitignore')
        if os.path.isfile(gitignore_path):
            with open(gitignore_path, 'r') as f:
                patterns.extend([
                    line.strip() 
                    for line in f 
                    if line.strip() and not line.startswith('#')
                ])
        
        return pathspec.PathSpec.from_lines('gitwildmatch', patterns)

    def _build_tree(self, path: str, prefix: str, spec: pathspec.PathSpec, 
                    root: str, depth: int, max_depth: Optional[int]) -> str:
        """Recursively build tree structure."""
        if max_depth is not None and depth > max_depth:
            return ""
        
        try:
            # Get and filter entries
            entries = []
            for entry in os.listdir(path):
                entry_path = os.path.join(path, entry)
                is_dir = os.path.isdir(entry_path)
                
                # Check if should ignore
                rel_path = os.path.relpath(entry_path, root)
                if is_dir:
                    rel_path += '/'
                if spec.match_file(rel_path):
                    continue
                
                entries.append((entry, is_dir, entry_path))
            
            # Sort: directories first, then alphabetically
            entries.sort(key=lambda x: (not x[1], x[0].lower()))
            
            # Build output
            lines = []
            for i, (entry, is_dir, entry_path) in enumerate(entries):
                is_last = (i == len(entries) - 1)
                connector = "└── " if is_last else "├── "
                name = entry + "/" if is_dir else entry
                
                lines.append(f"{prefix}{connector}{name}")
                
                # Recurse into directories
                if is_dir:
                    extension = "    " if is_last else "│   "
                    subtree = self._build_tree(
                        entry_path, prefix + extension, spec, 
                        root, depth + 1, max_depth
                    )
                    if subtree:
                        lines.append(subtree)
            
            return "\n".join(lines)
        
        except PermissionError:
            return f"{prefix}[Permission Denied]"
        except Exception as e:
            return f"{prefix}[Error: {e}]"

    def execute(self, path: str = ".", max_depth: Optional[int] = None) -> str:
        """Execute tree command."""
        try:
            path = os.path.abspath(path)
            
            if not os.path.isdir(path):
                return f"error: '{path}' is not a directory"
            
            spec = self._load_gitignore(path)
            tree = self._build_tree(path, "", spec, path, 0, max_depth)
            
            result = os.path.basename(path) or path + "/"
            if tree:
                result += "\n" + tree
            
            return result
        
        except Exception as e:
            return f"error: {e}"