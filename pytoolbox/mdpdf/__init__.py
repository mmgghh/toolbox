"""The pieces ``pymd2pdf`` uses to typeset Markdown into a PDF.

``state`` holds the settings a conversion shares, ``shaping`` reshapes and
reorders Persian/Arabic text, ``fonts`` finds the faces to draw it with,
``document`` is the fpdf2 subclass everything draws into, and ``render``,
``tables`` and ``media`` turn Markdown blocks into marks on the page.
``pymd2pdf`` itself keeps the conversion loop and the command line.
"""
