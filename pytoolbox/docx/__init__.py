"""Reading Word ``.docx`` files with nothing but the standard library.

A ``.docx`` is a zip of XML parts (the OPC container). The pieces here map one
to one onto that structure:

``package``    opens the zip and hands out parsed parts and relationships
``numbering``  turns ``numbering.xml`` into a list-level lookup
``styles``     turns ``styles.xml`` into the properties a paragraph inherits
``comments``   turns ``comments.xml`` into threaded ``Comment`` objects
``omml``       turns Word's equations into LaTeX
``symbols``    turns a symbol font's glyphs into ordinary Unicode text
``document``   walks ``document.xml`` into a flat list of blocks
``markdown``   turns those blocks, plus the comments, into Markdown

``markdown`` never touches XML and ``document`` never emits Markdown, so the
seam between them is a list of plain objects that tests can build by hand.
"""
