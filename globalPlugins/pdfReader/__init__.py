import gui
import wx
import ui
import webbrowser
import threading
import os
import sys
from scriptHandler import script
import globalPluginHandler
import globalVars
import addonHandler

base_path = os.path.dirname(__file__)
libs_path = os.path.join(base_path, "libs")

if libs_path not in sys.path:
    sys.path.append(libs_path)

try:
    import fitz
except ImportError:
    raise RuntimeError("PyMuPDF not found in libs directory")

addonHandler.initTranslation()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Characters of surrounding text included in every NVDA match announcement.
_CONTEXT_CHARS = 80


def extract_page_text(page):
    """
    Extract text from a PDF page using block-based extraction for better
    reading order and structure.  Falls back to plain get_text() when blocks
    yield nothing useful (e.g. image-only or malformed pages).
    """
    blocks = page.get_text("blocks")  # (x0, y0, x1, y1, text, block_no, block_type)
    blocks_sorted = sorted(blocks, key=lambda b: (round(b[1] / 10), b[0]))
    lines = []
    for block in blocks_sorted:
        if block[6] == 0:  # text block (not image)
            text = block[4].strip()
            if text:
                lines.append(text)
    result = "\n\n".join(lines)
    if not result.strip():
        result = page.get_text().strip()
    return result if result else _("[No readable text on this page]")


def _context_snippet(text, char_start, char_end, context=_CONTEXT_CHARS):
    """
    Return a short string centred on the match so NVDA reads the surrounding
    sentence, not just the bare matched word.

    Format:  "…before [MATCH] after…"
    """
    before_start = max(0, char_start - context)
    after_end = min(len(text), char_end + context)

    before = text[before_start:char_start].lstrip("\n")
    match  = text[char_start:char_end]
    after  = text[char_end:after_end].rstrip("\n")

    prefix = "…" if before_start > 0 else ""
    suffix = "…" if after_end < len(text) else ""

    return "{prefix}{before}[{match}]{after}{suffix}".format(
        prefix=prefix, before=before, match=match, after=after, suffix=suffix
    )


# ---------------------------------------------------------------------------
# Dialogs – Processing / About / Help
# ---------------------------------------------------------------------------

class ProcessingDialog(wx.Dialog):
    def __init__(self, parent, message=None):
        super(ProcessingDialog, self).__init__(parent, title=_("Processing..."))
        self.SetSize((340, 120))
        self.Centre()
        main_sizer = wx.BoxSizer(wx.VERTICAL)
        self.label = wx.StaticText(
            self, label=message or _("Please wait while the PDF is being processed...")
        )
        main_sizer.Add(self.label, 0, wx.ALL | wx.CENTER, 20)
        self.SetSizer(main_sizer)
        self.label.SetFocus()

    def set_message(self, message):
        wx.CallAfter(self.label.SetLabel, message)


class AboutDialog(wx.Dialog):
    def __init__(self, parent):
        super(AboutDialog, self).__init__(parent, title=_("About the PDF reader..."))
        self.SetSize((500, 300))
        self.Centre()
        main_sizer = wx.BoxSizer(wx.VERTICAL)
        message = wx.StaticText(
            self,
            label=_(
                "This add-on is designed for the blind and visually impaired to read "
                "their PDF accessibly. You can join our Telegram channel to get more resources."
            ),
        )
        message.Wrap(450)
        main_sizer.Add(message, 0, wx.ALL | wx.EXPAND, 15)
        button_sizer = wx.BoxSizer(wx.HORIZONTAL)
        telegram_button = wx.Button(self, label=_("Join &Telegram"))
        self.Bind(wx.EVT_BUTTON, self.on_telegram, telegram_button)
        button_sizer.Add(telegram_button, 0, wx.RIGHT, 10)
        back_button = wx.Button(self, label=_("&Back"))
        self.Bind(wx.EVT_BUTTON, self.on_back, back_button)
        button_sizer.Add(back_button, 0)
        main_sizer.Add(button_sizer, 0, wx.ALL | wx.CENTER, 15)
        self.SetSizer(main_sizer)
        telegram_button.SetFocus()

    def on_telegram(self, event):
        webbrowser.open("https://t.me/blindtechvisionary")

    def on_back(self, event):
        self.EndModal(wx.ID_OK)


class HelpDialog(wx.Dialog):
    def __init__(self, parent):
        super(HelpDialog, self).__init__(parent, title=_("Help for the PDF reader"))
        self.SetSize((520, 470))
        self.Centre()
        main_sizer = wx.BoxSizer(wx.VERTICAL)
        instructions = _(
            "Step by step instructions:\n\n"
            "1. Click 'Import PDF' to select a PDF file.\n"
            "2. Wait for processing to complete.\n"
            "3. Use Alt+N for the next page.\n"
            "4. Use Alt+P for the previous page.\n"
            "5. Use the page dropdown to jump to a specific page.\n\n"
            "Global Find (searches the entire document):\n"
            "6. Press Ctrl+F or click the 'Find' button to open the search bar.\n"
            "7. Type your search term — the add-on indexes all pages in the background.\n"
            "8. Press Enter or F3 to jump to the next match.\n"
            "9. Press Shift+F3 to jump to the previous match.\n"
            "10. NVDA will read the match count, page number, and surrounding\n"
            "    sentence context so you know exactly where you have landed.\n"
            "11. Press Escape to close the find bar.\n\n"
            "12. Use the Back button to return to the main menu.\n"
            "13. Use the Close button to exit the add-on."
        )
        message = wx.StaticText(self, label=instructions)
        message.Wrap(470)
        main_sizer.Add(message, 0, wx.ALL | wx.EXPAND, 15)
        back_button = wx.Button(self, label=_("&Back"))
        self.Bind(wx.EVT_BUTTON, self.on_back, back_button)
        main_sizer.Add(back_button, 0, wx.ALL | wx.CENTER, 15)
        self.SetSizer(main_sizer)
        back_button.SetFocus()

    def on_back(self, event):
        self.EndModal(wx.ID_OK)


# ---------------------------------------------------------------------------
# Find bar
# ---------------------------------------------------------------------------

class FindBar(wx.Panel):
    """
    Collapsible find-bar panel.  All search logic lives in PdfDialog; this
    panel only handles user input and result/progress display.
    """

    def __init__(self, parent, on_find_next, on_find_prev, on_close_cb):
        super(FindBar, self).__init__(parent)
        self._on_find_next = on_find_next
        self._on_find_prev = on_find_prev
        self._on_close_cb = on_close_cb

        sizer = wx.BoxSizer(wx.HORIZONTAL)

        label = wx.StaticText(self, label=_("Find:"))
        sizer.Add(label, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 5)

        self.search_ctrl = wx.TextCtrl(self, style=wx.TE_PROCESS_ENTER)
        self.search_ctrl.SetHint(_("Search entire document…"))
        sizer.Add(self.search_ctrl, 1, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 5)

        # Live status: "Indexing page 3 of 50…" / "Match 2 of 17" / "Not found"
        self.result_label = wx.StaticText(self, label="")
        sizer.Add(self.result_label, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 10)

        prev_btn = wx.Button(self, label=_("◀ &Prev  Shift+F3"))
        sizer.Add(prev_btn, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 5)

        next_btn = wx.Button(self, label=_("▶ &Next  F3"))
        sizer.Add(next_btn, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 5)

        close_btn = wx.Button(self, label="✕", size=(28, -1))
        close_btn.SetToolTip(_("Close find bar  (Escape)"))
        sizer.Add(close_btn, 0, wx.ALIGN_CENTER_VERTICAL)

        self.SetSizer(sizer)

        self.search_ctrl.Bind(wx.EVT_TEXT_ENTER, lambda e: self._on_find_next())
        self.search_ctrl.Bind(wx.EVT_KEY_DOWN, self._on_key_down)
        next_btn.Bind(wx.EVT_BUTTON, lambda e: self._on_find_next())
        prev_btn.Bind(wx.EVT_BUTTON, lambda e: self._on_find_prev())
        close_btn.Bind(wx.EVT_BUTTON, lambda e: self._on_close_cb())

    def _on_key_down(self, event):
        key = event.GetKeyCode()
        if key == wx.WXK_ESCAPE:
            self._on_close_cb()
        elif key == wx.WXK_F3:
            if event.ShiftDown():
                self._on_find_prev()
            else:
                self._on_find_next()
        else:
            event.Skip()

    def get_query(self):
        return self.search_ctrl.GetValue().strip()

    def set_result(self, text):
        self.result_label.SetLabel(text)
        self.Layout()

    def focus(self):
        self.search_ctrl.SetFocus()
        self.search_ctrl.SelectAll()


# ---------------------------------------------------------------------------
# PDF reader dialog
# ---------------------------------------------------------------------------

class PdfDialog(wx.Dialog):
    def __init__(self, parent, pdf_doc, pdf_path=""):
        super(PdfDialog, self).__init__(parent, title=_("PDF reader panel"))
        self.pdf_doc  = pdf_doc
        self.pdf_path = pdf_path
        self.current_page = 0
        self.total_pages  = len(pdf_doc)
        self.SetSize((660, 580))
        self.Centre()

        # Page-text cache – populated on demand and during background indexing.
        self._page_cache  = {}
        self._cache_lock  = threading.Lock()

        # ---- Find state ----
        # _find_matches : list[(page_num, char_start, char_end)] across ALL pages
        # _last_query   : query string that produced _find_matches (used to detect stale cache)
        # _find_index   : current position in _find_matches  (-1 = not yet jumped)
        # _find_thread  : background indexing thread, or None
        # _find_abort   : threading.Event – set it to cancel the running thread
        self._find_matches = []
        self._last_query   = ""
        self._find_index   = -1
        self._find_thread  = None
        self._find_abort   = threading.Event()

        # ---- Build UI ----
        main_sizer = wx.BoxSizer(wx.VERTICAL)

        self.status_text = wx.StaticText(self, label="")
        main_sizer.Add(self.status_text, 0, wx.ALL | wx.EXPAND, 8)

        self.text_ctrl = wx.TextCtrl(
            self,
            style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_RICH2 | wx.TE_NOHIDESEL,
        )
        self.text_ctrl.Bind(wx.EVT_KEY_DOWN, self._on_text_key_down)
        main_sizer.Add(self.text_ctrl, 1, wx.LEFT | wx.RIGHT | wx.EXPAND, 8)

        self.find_bar = FindBar(
            self,
            on_find_next=self._find_next,
            on_find_prev=self._find_prev,
            on_close_cb=self._close_find_bar,
        )
        self.find_bar.Hide()
        # Invalidate the match cache whenever the user edits the search field.
        self.find_bar.search_ctrl.Bind(wx.EVT_TEXT, self._on_find_query_changed)
        main_sizer.Add(self.find_bar, 0, wx.ALL | wx.EXPAND, 4)

        nav_sizer = wx.BoxSizer(wx.HORIZONTAL)

        prev_button = wx.Button(self, label=_("&Previous Page  Alt+P"))
        prev_button.Bind(wx.EVT_BUTTON, self.on_prev)
        nav_sizer.Add(prev_button, 0, wx.RIGHT, 8)

        next_button = wx.Button(self, label=_("&Next Page  Alt+N"))
        next_button.Bind(wx.EVT_BUTTON, self.on_next)
        nav_sizer.Add(next_button, 0, wx.RIGHT, 8)

        page_label = wx.StaticText(self, label=_("Go to page:"))
        nav_sizer.Add(page_label, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 5)

        self.page_choice = wx.Choice(
            self, choices=[str(i + 1) for i in range(self.total_pages)]
        )
        self.page_choice.Bind(wx.EVT_CHOICE, self.on_page_change)
        nav_sizer.Add(self.page_choice, 0, wx.RIGHT, 8)

        find_button = wx.Button(self, label=_("&Find…  Ctrl+F"))
        find_button.Bind(wx.EVT_BUTTON, lambda e: self._open_find_bar())
        nav_sizer.Add(find_button, 0)

        main_sizer.Add(nav_sizer, 0, wx.ALL | wx.CENTER, 8)

        button_sizer = wx.BoxSizer(wx.HORIZONTAL)
        back_button  = wx.Button(self, label=_("&Back"))
        back_button.Bind(wx.EVT_BUTTON, self.on_back)
        button_sizer.Add(back_button, 0, wx.RIGHT, 10)
        close_button = wx.Button(self, label=_("&Close"))
        close_button.Bind(wx.EVT_BUTTON, self.on_close)
        button_sizer.Add(close_button, 0)
        main_sizer.Add(button_sizer, 0, wx.ALL | wx.CENTER, 8)

        self.SetSizer(main_sizer)

        # Accelerators: Ctrl+F, F3, Shift+F3
        id_open = wx.NewId()
        id_next = wx.NewId()
        id_prev = wx.NewId()
        self.SetAcceleratorTable(wx.AcceleratorTable([
            wx.AcceleratorEntry(wx.ACCEL_CTRL,   ord("F"),      id_open),
            wx.AcceleratorEntry(wx.ACCEL_NORMAL,  wx.WXK_F3,   id_next),
            wx.AcceleratorEntry(wx.ACCEL_SHIFT,   wx.WXK_F3,   id_prev),
        ]))
        self.Bind(wx.EVT_MENU, lambda e: self._open_find_bar(), id=id_open)
        self.Bind(wx.EVT_MENU, lambda e: self._find_next(),     id=id_next)
        self.Bind(wx.EVT_MENU, lambda e: self._find_prev(),     id=id_prev)

        self.load_page(0)
        self.status_text.SetFocus()

    # ------------------------------------------------------------------
    # Page text extraction & caching
    # ------------------------------------------------------------------

    def _get_page_text(self, page_num):
        with self._cache_lock:
            if page_num not in self._page_cache:
                page = self.pdf_doc.load_page(page_num)
                self._page_cache[page_num] = extract_page_text(page)
            return self._page_cache[page_num]

    # ------------------------------------------------------------------
    # Status & page loading
    # ------------------------------------------------------------------

    def update_status(self):
        status = _("Page {current} of {total}").format(
            current=self.current_page + 1, total=self.total_pages
        )
        if self.pdf_path:
            status = os.path.basename(self.pdf_path) + "  —  " + status
        self.status_text.SetLabel(status)
        ui.message(
            _("Page {current} of {total}").format(
                current=self.current_page + 1, total=self.total_pages
            )
        )

    def load_page(self, page_num):
        if 0 <= page_num < self.total_pages:
            self.current_page = page_num
            text = self._get_page_text(page_num)
            self.text_ctrl.SetValue(text)
            self.page_choice.SetSelection(page_num)
            self.update_status()
            self._clear_highlights()

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    def on_prev(self, event):
        if self.current_page > 0:
            self.load_page(self.current_page - 1)
        else:
            ui.message(_("You are on the first page. No previous page available."))

    def on_next(self, event):
        if self.current_page < self.total_pages - 1:
            self.load_page(self.current_page + 1)
        else:
            ui.message(_("You are on the last page. No next page available."))

    def on_page_change(self, event):
        self.load_page(self.page_choice.GetSelection())

    def _on_text_key_down(self, event):
        key = event.GetKeyCode()
        if event.AltDown():
            if key == ord("N"):
                self.on_next(None)
                return
            elif key == ord("P"):
                self.on_prev(None)
                return
        event.Skip()

    # ------------------------------------------------------------------
    # Find bar open / close
    # ------------------------------------------------------------------

    def _open_find_bar(self):
        self.find_bar.Show()
        self.Layout()
        self.find_bar.focus()

    def _close_find_bar(self):
        self._abort_find_thread()
        self.find_bar.Hide()
        self.Layout()
        self._clear_highlights()
        self._find_matches = []
        self._last_query   = ""
        self._find_index   = -1
        self.text_ctrl.SetFocus()

    # ------------------------------------------------------------------
    # Background indexing – global find across ALL pages
    # ------------------------------------------------------------------

    def _on_find_query_changed(self, event):
        """Invalidate the match cache whenever the user edits the search field."""
        self._abort_find_thread()
        self._find_matches = []
        self._last_query   = ""
        self._find_index   = -1
        self.find_bar.set_result("")
        event.Skip()

    def _abort_find_thread(self):
        if self._find_thread and self._find_thread.is_alive():
            self._find_abort.set()
            self._find_thread.join(timeout=1.0)
        self._find_abort.clear()
        self._find_thread = None

    def _start_index_thread(self, query, on_done):
        """
        Spin up a background thread that searches every page for `query`.
        Calls `on_done(matches)` on the main thread when finished.
        Progress is shown live in the find-bar label.
        """
        self._abort_find_thread()
        abort_event = self._find_abort
        total = self.total_pages

        def run():
            matches = []
            q = query.lower()
            for p in range(total):
                if abort_event.is_set():
                    return  # abandoned; do NOT call on_done
                text       = self._get_page_text(p)
                lower_text = text.lower()
                start = 0
                while True:
                    idx = lower_text.find(q, start)
                    if idx == -1:
                        break
                    matches.append((p, idx, idx + len(q)))
                    start = idx + 1
                # Update progress label (safe – wx.CallAfter posts to main thread)
                wx.CallAfter(
                    self.find_bar.set_result,
                    _("Indexing page {current} of {total}…").format(
                        current=p + 1, total=total
                    ),
                )
            wx.CallAfter(on_done, matches)

        self._find_thread = threading.Thread(target=run, daemon=True)
        self._find_thread.start()

    def _ensure_matches(self, query, callback):
        """
        If a valid match list already exists for `query`, call callback immediately.
        Otherwise start background indexing and call callback when done.
        """
        if query == self._last_query and self._find_matches is not None:
            callback(self._find_matches)
            return

        # Reset and kick off a fresh index run.
        self._find_matches = []
        self._last_query   = query
        self.find_bar.set_result(_("Indexing…"))

        def on_done(matches):
            self._find_matches = matches
            callback(matches)

        self._start_index_thread(query, on_done)

    # ------------------------------------------------------------------
    # Find next / prev (called from find bar and accelerators)
    # ------------------------------------------------------------------

    def _find_next(self):
        query = self.find_bar.get_query()
        if not query:
            ui.message(_("Please enter a search term."))
            return

        def after_index(matches):
            if not matches:
                self.find_bar.set_result(_("Not found"))
                ui.message(_("'{query}' was not found in this document.").format(query=query))
                return
            old_index        = self._find_index
            self._find_index = (self._find_index + 1) % len(matches)
            wrapped          = old_index >= 0 and self._find_index < old_index
            self._jump_to_match(self._find_index, wrapped=wrapped, direction="next")

        self._ensure_matches(query, after_index)

    def _find_prev(self):
        query = self.find_bar.get_query()
        if not query:
            ui.message(_("Please enter a search term."))
            return

        def after_index(matches):
            if not matches:
                self.find_bar.set_result(_("Not found"))
                ui.message(_("'{query}' was not found in this document.").format(query=query))
                return
            if self._find_index <= 0:
                self._find_index = len(matches) - 1
                wrapped          = True
            else:
                self._find_index -= 1
                wrapped           = False
            self._jump_to_match(self._find_index, wrapped=wrapped, direction="prev")

        self._ensure_matches(query, after_index)

    # ------------------------------------------------------------------
    # Jump to a specific match
    # ------------------------------------------------------------------

    def _jump_to_match(self, index, wrapped=False, direction="next"):
        matches              = self._find_matches
        page_num, char_start, char_end = matches[index]
        total                = len(matches)

        result_msg = _("Match {current} of {total}  —  page {page}").format(
            current=index + 1, total=total, page=page_num + 1
        )
        self.find_bar.set_result(result_msg)

        # Navigate to the page if the match is on a different page.
        if page_num != self.current_page:
            self.current_page = page_num
            text = self._get_page_text(page_num)
            self.text_ctrl.SetValue(text)
            self.page_choice.SetSelection(page_num)
            self.update_status()

        # Yellow highlight on the matched text.
        self._clear_highlights()
        self.text_ctrl.SetStyle(
            char_start, char_end,
            wx.TextAttr(wx.BLACK, wx.Colour(255, 220, 0)),
        )
        self.text_ctrl.ShowPosition(char_start)
        self.text_ctrl.SetSelection(char_start, char_end)

        # ---- Accessible NVDA announcement ----
        # Read ~80 chars of surrounding text so the user hears the sentence
        # context around the match, not just its position.
        page_text = self._get_page_text(page_num)
        snippet   = _context_snippet(page_text, char_start, char_end)

        wrap_notice = ""
        if wrapped:
            wrap_notice = (
                _("Wrapped to beginning. ") if direction == "next"
                else _("Wrapped to end. ")
            )

        ui.message(
            _("{wrap}Match {current} of {total}, page {page}. {snippet}").format(
                wrap    = wrap_notice,
                current = index + 1,
                total   = total,
                page    = page_num + 1,
                snippet = snippet,
            )
        )

    # ------------------------------------------------------------------
    # Highlight helpers
    # ------------------------------------------------------------------

    def _clear_highlights(self):
        length = self.text_ctrl.GetLastPosition()
        if length > 0:
            self.text_ctrl.SetStyle(0, length, wx.TextAttr(wx.NullColour, wx.NullColour))

    # ------------------------------------------------------------------
    # Dialog close
    # ------------------------------------------------------------------

    def on_back(self, event):
        self._abort_find_thread()
        self.EndModal(wx.ID_BACK)

    def on_close(self, event):
        self._abort_find_thread()
        self.EndModal(wx.ID_CLOSE)


# ---------------------------------------------------------------------------
# Main dialog
# ---------------------------------------------------------------------------

class MainDialog(wx.Dialog):
    def __init__(self, parent):
        super(MainDialog, self).__init__(parent, title=_("PDF Reader"))
        self.SetSize((400, 300))
        self.Centre()

        main_sizer = wx.BoxSizer(wx.VERTICAL)

        import_button = wx.Button(self, label=_("&Import PDF"))
        self.Bind(wx.EVT_BUTTON, self.on_import, import_button)
        main_sizer.Add(import_button, 0, wx.ALL | wx.EXPAND, 15)

        about_button = wx.Button(self, label=_("&About"))
        self.Bind(wx.EVT_BUTTON, self.on_about, about_button)
        main_sizer.Add(about_button, 0, wx.ALL | wx.EXPAND, 15)

        help_button = wx.Button(self, label=_("&Help"))
        self.Bind(wx.EVT_BUTTON, self.on_help, help_button)
        main_sizer.Add(help_button, 0, wx.ALL | wx.EXPAND, 15)

        close_button = wx.Button(self, label=_("&Close"))
        self.Bind(wx.EVT_BUTTON, self.on_close, close_button)
        main_sizer.Add(close_button, 0, wx.ALL | wx.EXPAND, 15)

        self.SetSizer(main_sizer)
        import_button.SetFocus()

    def on_import(self, event):
        with wx.FileDialog(
            self,
            _("Choose PDF file"),
            wildcard="PDF files (*.pdf)|*.pdf",
        ) as file_dialog:
            if file_dialog.ShowModal() == wx.ID_OK:
                self.process_pdf(file_dialog.GetPath())

    def process_pdf(self, pdf_path):
        processing_dialog = ProcessingDialog(self)

        def extract_pdf():
            try:
                pdf_doc = fitz.open(pdf_path)
                wx.CallAfter(self.show_pdf_dialog, pdf_doc, pdf_path)
            except Exception as e:
                wx.CallAfter(
                    ui.message,
                    _("Error processing PDF: {error}").format(error=str(e)),
                )
            finally:
                wx.CallAfter(processing_dialog.Destroy)

        processing_dialog.Show()
        threading.Thread(target=extract_pdf, daemon=True).start()

    def show_pdf_dialog(self, pdf_doc, pdf_path=""):
        pdf_dialog = PdfDialog(self, pdf_doc, pdf_path=pdf_path)
        result = pdf_dialog.ShowModal()
        pdf_doc.close()
        if result == wx.ID_CLOSE:
            self.Close()

    def on_about(self, event):
        dlg = AboutDialog(self)
        dlg.ShowModal()
        dlg.Destroy()

    def on_help(self, event):
        dlg = HelpDialog(self)
        dlg.ShowModal()
        dlg.Destroy()

    def on_close(self, event):
        self.Close()


# ---------------------------------------------------------------------------
# Global plugin
# ---------------------------------------------------------------------------

class GlobalPlugin(globalPluginHandler.GlobalPlugin):
    def __init__(self):
        super(GlobalPlugin, self).__init__()
        if globalVars.appArgs.secure:
            return
        self.main_dialog = None
        self.create_menu()

    def create_menu(self):
        self.tools_menu    = gui.mainFrame.sysTrayIcon.toolsMenu
        self.pdf_reader_item = self.tools_menu.Append(
            wx.ID_ANY, _("PDF &Reader"), _("Open PDF Reader"),
        )
        gui.mainFrame.sysTrayIcon.Bind(
            wx.EVT_MENU, self.on_tools_menu_pdf_reader, self.pdf_reader_item,
        )

    def on_tools_menu_pdf_reader(self, event):
        self.script_show_main_dialog(None)

    @script(
        description=_("Open PDF Reader"),
        category=_("PDF Reader"),
        gesture="kb:NVDA+alt+p",
    )
    def script_show_main_dialog(self, gesture):
        if self.main_dialog:
            self.main_dialog.Raise()
            return
        gui.mainFrame.prePopup()
        self.main_dialog = MainDialog(gui.mainFrame)
        self.main_dialog.Show()
        self.main_dialog.Bind(wx.EVT_CLOSE, self.on_main_dialog_close)
        gui.mainFrame.postPopup()

    def on_main_dialog_close(self, event):
        if self.main_dialog:
            self.main_dialog.Destroy()
            self.main_dialog = None
        gui.mainFrame.postPopup()

    def terminate(self):
        if self.main_dialog:
            self.main_dialog.Destroy()
            self.main_dialog = None
        try:
            if self.pdf_reader_item:
                self.tools_menu.Remove(self.pdf_reader_item)
        except Exception:
            pass