"""Reading a PDF's text as positioned runs, and inferring structure from them.

A PDF stores placed glyphs, not structure: nothing in the file says "heading".
Everything above :mod:`~pytoolbox.pdf.reader` is inference, and each rule is
chosen to fail towards plain text rather than towards mangled text.
"""
