# PrincessFinder 👑

Modern Linux file manager focused on **speed, productivity and elegant desktop workflows**.

PrincessFinder reimagines the traditional file explorer by combining a clean interface, powerful file management tools and seamless integration with modern Linux development environments.

---

<p align="center">
  <img src="docs/demo.gif" alt="PrincessFinder Demo" width="100%">
</p>

---

# Features

## 📁 File Management

- Browse local folders
- Multiple navigation modes
- Breadcrumb navigation
- Drag & Drop
- Copy, Move, Rename and Delete
- Create files and folders

## ⚡ Productivity

- Instant file search
- Real-time file updates
- Favorites
- Bookmarks
- Recent locations
- Clipboard operations

## 🖥 Interface

- Modern desktop interface
- Dockable sidebars
- Resizable panels
- Multiple tabs
- Light & Dark themes
- Split View *(planned)*

## 💻 Developer Tools

- Open folders directly in VS Code
- Launch terminal from the current directory
- Git-aware navigation *(planned)*

## 🚀 Performance

- Fast directory loading
- Lazy thumbnail generation
- Responsive interface
- Large folder optimization

---

# Tech Stack

- Python
- PySide6
- Qt6
- Linux

---

# Application Architecture

```
Filesystem
      │
      ▼
Directory Scanner
      │
      ▼
File Model
      │
      ▼
Navigation Engine
      │
      ▼
Qt Interface
      │
      ▼
User Actions
```

---

# Roadmap

## File Management

- [ ] Split View
- [ ] Preview Panel
- [ ] Batch Rename
- [ ] Archive Browsing

## Development

- [ ] Git Integration
- [ ] SSH / SFTP Browser
- [ ] Plugin System

## Media

- [ ] Image Metadata Viewer
- [ ] Better Thumbnail Engine

## Customization

- [ ] Custom Themes
- [ ] Layout Profiles

---

# Installation

```bash
git clone https://github.com/princessnvidia/PrincessFinder.git

cd PrincessFinder

pip install -r requirements.txt

python princessfinder.py
```

---

# Philosophy

PrincessFinder is built around a simple principle:

File management should feel effortless.

Instead of overwhelming users with unnecessary complexity, the application focuses on fast navigation, intuitive organization and developer-friendly workflows while remaining lightweight and fully open source.

The long-term vision is to create a modern Linux file manager that combines the elegance of commercial desktop applications with the flexibility and openness of the Linux ecosystem.

---

# Inspiration

- Finder
- Dolphin
- Files (GNOME)
- Directory Opus
- Total Commander
- Path Finder

---

# Status

🚧 Active Development

---

# License

MIT License
