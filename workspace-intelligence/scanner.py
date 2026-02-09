"""
Workspace Intelligence Layer - Workspace Scanner

Discovers project roots within a workspace by detecting marker files.
"""

import os
from pathlib import Path
from typing import List, Dict, Set, Optional
from dataclasses import dataclass, field
from enum import Enum


class ProjectType(str, Enum):
    """Detected project type based on marker files."""
    NODEJS = "nodejs"
    PYTHON = "python"
    GO = "go"
    RUST = "rust"
    JAVA = "java"
    DOTNET = "dotnet"
    DOCKER = "docker"
    UNKNOWN = "unknown"


# Marker files that indicate a project root
PROJECT_MARKERS: Dict[str, ProjectType] = {
    "package.json": ProjectType.NODEJS,
    "pyproject.toml": ProjectType.PYTHON,
    "setup.py": ProjectType.PYTHON,
    "requirements.txt": ProjectType.PYTHON,
    "go.mod": ProjectType.GO,
    "Cargo.toml": ProjectType.RUST,
    "pom.xml": ProjectType.JAVA,
    "build.gradle": ProjectType.JAVA,
    "*.csproj": ProjectType.DOTNET,
    "*.sln": ProjectType.DOTNET,
}

# Infrastructure markers (not full projects, but important)
INFRA_MARKERS: Set[str] = {
    "Dockerfile",
    "docker-compose.yml",
    "docker-compose.yaml",
    "kubernetes",
    "k8s",
    "terraform",
    ".env.example",
}

# Directories to skip during scanning
SKIP_DIRS: Set[str] = {
    "node_modules",
    ".git",
    ".svn",
    "__pycache__",
    ".venv",
    "venv",
    ".idea",
    ".vscode",
    "dist",
    "build",
    "target",
    ".next",
    ".nuxt",
}


@dataclass
class DiscoveredProject:
    """A discovered project within the workspace."""
    path: Path
    name: str
    project_type: ProjectType
    marker_file: str
    has_git: bool = False
    infra_files: List[str] = field(default_factory=list)


@dataclass
class ScanResult:
    """Result of scanning a workspace."""
    workspace_root: Path
    projects: List[DiscoveredProject]
    infra_paths: List[Path]
    total_files_scanned: int = 0


class WorkspaceScanner:
    """
    Scans a workspace directory to discover project roots and infrastructure.
    
    Uses heuristic-based detection via marker files (package.json, pyproject.toml, etc.)
    """
    
    def __init__(self, workspace_root: str | Path, max_depth: int = 5):
        self.workspace_root = Path(workspace_root).resolve()
        self.max_depth = max_depth
        self._files_scanned = 0
    
    def scan(self) -> ScanResult:
        """
        Scan the workspace and return discovered projects.
        
        Returns:
            ScanResult with all discovered projects and infra paths
        """
        projects: List[DiscoveredProject] = []
        infra_paths: List[Path] = []
        self._files_scanned = 0
        
        self._scan_directory(
            self.workspace_root, 
            depth=0, 
            projects=projects, 
            infra_paths=infra_paths
        )
        
        return ScanResult(
            workspace_root=self.workspace_root,
            projects=projects,
            infra_paths=infra_paths,
            total_files_scanned=self._files_scanned,
        )
    
    def _scan_directory(
        self, 
        directory: Path, 
        depth: int,
        projects: List[DiscoveredProject],
        infra_paths: List[Path],
    ) -> Optional[DiscoveredProject]:
        """Recursively scan a directory for project markers."""
        
        if depth > self.max_depth:
            return None
        
        if directory.name in SKIP_DIRS:
            return None
        
        try:
            entries = list(directory.iterdir())
        except PermissionError:
            return None
        
        self._files_scanned += len(entries)
        
        # Check for project markers
        found_marker: Optional[str] = None
        found_type: ProjectType = ProjectType.UNKNOWN
        found_infra: List[str] = []
        has_git = False
        
        for entry in entries:
            name = entry.name
            
            # Check for .git
            if name == ".git" and entry.is_dir():
                has_git = True
            
            # Check for project markers
            if entry.is_file():
                if name in PROJECT_MARKERS:
                    found_marker = name
                    found_type = PROJECT_MARKERS[name]
                
                # Check for infra files
                if name in INFRA_MARKERS:
                    found_infra.append(name)
                    infra_paths.append(entry)
            
            # Check for infra directories
            if entry.is_dir() and name in INFRA_MARKERS:
                found_infra.append(name)
                infra_paths.append(entry)
        
        # If we found a project marker, record this as a project
        if found_marker:
            project = DiscoveredProject(
                path=directory,
                name=directory.name,
                project_type=found_type,
                marker_file=found_marker,
                has_git=has_git,
                infra_files=found_infra,
            )
            projects.append(project)
            # Don't recurse into project subdirectories (they're part of this project)
            return project
        
        # Otherwise, recurse into subdirectories
        for entry in entries:
            if entry.is_dir() and entry.name not in SKIP_DIRS:
                self._scan_directory(entry, depth + 1, projects, infra_paths)
        
        return None


# =============================================================================
# CLI Entry Point
# =============================================================================

if __name__ == "__main__":
    import sys
    
    if len(sys.argv) < 2:
        print("Usage: python scanner.py <workspace_path>")
        sys.exit(1)
    
    workspace_path = sys.argv[1]
    scanner = WorkspaceScanner(workspace_path)
    result = scanner.scan()
    
    print(f"\n{'='*60}")
    print(f"Workspace: {result.workspace_root}")
    print(f"Files scanned: {result.total_files_scanned}")
    print(f"Projects found: {len(result.projects)}")
    print(f"{'='*60}\n")
    
    for project in result.projects:
        print(f"  [{project.project_type.value}] {project.name}")
        print(f"      Path: {project.path}")
        print(f"      Marker: {project.marker_file}")
        if project.has_git:
            print(f"      Git: ✓")
        if project.infra_files:
            print(f"      Infra: {', '.join(project.infra_files)}")
        print()
