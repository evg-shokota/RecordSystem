import { Editor } from '@tiptap/core'
import StarterKit from '@tiptap/starter-kit'
import TextAlign from '@tiptap/extension-text-align'
import { TextStyle, FontFamily, FontSize } from '@tiptap/extension-text-style'
import { Color } from '@tiptap/extension-color'
import { Table, TableRow, TableCell, TableHeader } from '@tiptap/extension-table'
import Underline from '@tiptap/extension-underline'

window.TiptapEditor = Editor
window.TiptapExtensions = {
  StarterKit,
  TextAlign,
  FontFamily,
  FontSize,
  TextStyle,
  Color,
  Table,
  TableRow,
  TableCell,
  TableHeader,
  Underline,
}
