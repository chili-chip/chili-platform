"""Markdown editing in the Django admin.

The editor is EasyMDE (django-easymde). It previews in the browser, so it
needs no server endpoint and no Pillow, which the Worker does not have.
"""

from __future__ import annotations

from django import forms
from easymde.widgets import EasyMDEEditor

# EasyMDE would load Font Awesome from a CDN for its toolbar icons. A copy is
# served from src/static instead (EASYMDE_OPTIONS turns the download off).
FONT_AWESOME_CSS = "admin-markdown/font-awesome/css/font-awesome.min.css"
# Undoes admin form styles inside the editor's preview.
EDITOR_CSS = "admin-markdown/editor.css"


class MarkdownEditor(EasyMDEEditor):
    @property
    def media(self):
        return forms.Media(css={"all": (FONT_AWESOME_CSS,)}) + super().media + forms.Media(
            css={"all": (EDITOR_CSS,)}
        )


class MarkdownAdminMixin:
    """Edit the TextFields named in `markdown_fields` with the markdown editor."""

    markdown_fields: tuple[str, ...] = ()

    def formfield_for_dbfield(self, db_field, request, **kwargs):
        if db_field.name in self.markdown_fields:
            kwargs["widget"] = MarkdownEditor()
        return super().formfield_for_dbfield(db_field, request, **kwargs)
