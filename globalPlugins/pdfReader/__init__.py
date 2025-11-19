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

class ProcessingDialog(wx.Dialog):
    def __init__(self, parent):
        super(ProcessingDialog, self).__init__(parent, title=_("Processing..."))
        self.SetSize((300, 120))
        self.Centre()
        
        main_sizer = wx.BoxSizer(wx.VERTICAL)
        static_text = wx.StaticText(self, label=_("Please wait..."))
        main_sizer.Add(static_text, 0, wx.ALL | wx.CENTER, 20)
        self.SetSizer(main_sizer)
        self.static_text = static_text
        static_text.SetFocus()

class AboutDialog(wx.Dialog):
    def __init__(self, parent):
        super(AboutDialog, self).__init__(parent, title=_("About the PDF reader..."))
        self.SetSize((500, 300))
        self.Centre()
        
        main_sizer = wx.BoxSizer(wx.VERTICAL)
        
        message = wx.StaticText(self, label=_("This add-on is designed for the blind and visually impaired to read their PDF accessibly. You can join our telegram channel to get more resources."))
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
        self.SetSize((500, 400))
        self.Centre()
        
        main_sizer = wx.BoxSizer(wx.VERTICAL)
        
        instructions = _(
            "Step by step instructions:\n\n"
            "1. Click 'Import PDF' to select a PDF file\n"
            "2. Wait for processing to complete\n"
            "3. Use Alt+N for next page\n"
            "4. Use Alt+P for previous page\n"
            "5. Use dropdown to jump to specific pages\n"
            "6. Use Back button to return to main menu\n"
            "7. Use Close button to exit the add-on"
        )
        
        message = wx.StaticText(self, label=instructions)
        message.Wrap(450)
        main_sizer.Add(message, 0, wx.ALL | wx.EXPAND, 15)
        
        back_button = wx.Button(self, label=_("&Back"))
        self.Bind(wx.EVT_BUTTON, self.on_back, back_button)
        main_sizer.Add(back_button, 0, wx.ALL | wx.CENTER, 15)
        
        self.SetSizer(main_sizer)
        back_button.SetFocus()

    def on_back(self, event):
        self.EndModal(wx.ID_OK)

class PdfDialog(wx.Dialog):
    def __init__(self, parent, pdf_doc):
        super(PdfDialog, self).__init__(parent, title=_("PDF reader panel"))
        self.pdf_doc = pdf_doc
        self.current_page = 0
        self.total_pages = len(pdf_doc)
        self.SetSize((600, 500))
        self.Centre()
        
        main_sizer = wx.BoxSizer(wx.VERTICAL)
        
        self.status_text = wx.StaticText(self, label="")
        main_sizer.Add(self.status_text, 0, wx.ALL | wx.EXPAND, 10)
        
        self.text_ctrl = wx.TextCtrl(self, style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_RICH2)
        main_sizer.Add(self.text_ctrl, 1, wx.ALL | wx.EXPAND, 10)
        
        nav_sizer = wx.BoxSizer(wx.HORIZONTAL)
        
        prev_button = wx.Button(self, label=_("&Previous Page (Alt+P)"))
        self.Bind(wx.EVT_BUTTON, self.on_prev, prev_button)
        nav_sizer.Add(prev_button, 0, wx.RIGHT, 10)
        
        next_button = wx.Button(self, label=_("&Next Page (Alt+N)"))
        self.Bind(wx.EVT_BUTTON, self.on_next, next_button)
        nav_sizer.Add(next_button, 0, wx.RIGHT, 10)
        
        page_label = wx.StaticText(self, label=_("Go to:"))
        nav_sizer.Add(page_label, 0, wx.RIGHT | wx.CENTER, 5)
        
        self.page_choice = wx.Choice(self, choices=[str(i+1) for i in range(self.total_pages)])
        self.Bind(wx.EVT_CHOICE, self.on_page_change, self.page_choice)
        nav_sizer.Add(self.page_choice, 0, wx.RIGHT, 10)
        
        main_sizer.Add(nav_sizer, 0, wx.ALL | wx.CENTER, 10)
        
        button_sizer = wx.BoxSizer(wx.HORIZONTAL)
        
        back_button = wx.Button(self, label=_("&Back"))
        self.Bind(wx.EVT_BUTTON, self.on_back, back_button)
        button_sizer.Add(back_button, 0, wx.RIGHT, 10)
        
        close_button = wx.Button(self, label=_("&Close"))
        self.Bind(wx.EVT_BUTTON, self.on_close, close_button)
        button_sizer.Add(close_button, 0)
        
        main_sizer.Add(button_sizer, 0, wx.ALL | wx.CENTER, 10)
        self.SetSizer(main_sizer)
        
        self.load_page(0)
        self.status_text.SetFocus()

    def update_status(self):
        status = _("Total pages: {total_pages}, Current page: {current_page}").format(
            total_pages=self.total_pages, current_page=self.current_page + 1
        )
        self.status_text.SetLabel(status)

    def load_page(self, page_num):
        if 0 <= page_num < self.total_pages:
            self.current_page = page_num
            page = self.pdf_doc.load_page(page_num)
            text = page.get_text()
            self.text_ctrl.SetValue(text)
            self.page_choice.SetSelection(page_num)
            self.update_status()

    def on_prev(self, event):
        if self.current_page > 0:
            self.load_page(self.current_page - 1)
        else:
            ui.message(_("No previous page is available"))

    def on_next(self, event):
        if self.current_page < self.total_pages - 1:
            self.load_page(self.current_page + 1)
        else:
            ui.message(_("Next page is not available"))

    def on_page_change(self, event):
        selected_page = self.page_choice.GetSelection()
        self.load_page(selected_page)

    def on_back(self, event):
        self.EndModal(wx.ID_BACK)

    def on_close(self, event):
        self.EndModal(wx.ID_CLOSE)

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
        with wx.FileDialog(self, _("Choose PDF file"), wildcard="PDF files (*.pdf)|*.pdf") as file_dialog:
            if file_dialog.ShowModal() == wx.ID_OK:
                pdf_path = file_dialog.GetPath()
                self.process_pdf(pdf_path)

    def process_pdf(self, pdf_path):
        processing_dialog = ProcessingDialog(self)
        
        def extract_pdf():
            try:
                pdf_doc = fitz.open(pdf_path)
                wx.CallAfter(self.show_pdf_dialog, pdf_doc)
            except Exception as e:
                wx.CallAfter(ui.message, _("Error processing PDF: {error}").format(error=str(e)))
            finally:
                wx.CallAfter(processing_dialog.Destroy)
        
        processing_dialog.Show()
        thread = threading.Thread(target=extract_pdf)
        thread.daemon = True
        thread.start()

    def show_pdf_dialog(self, pdf_doc):
        pdf_dialog = PdfDialog(self, pdf_doc)
        result = pdf_dialog.ShowModal()
        pdf_doc.close()
        
        if result == wx.ID_CLOSE:
            self.Close()

    def on_about(self, event):
        about_dialog = AboutDialog(self)
        about_dialog.ShowModal()
        about_dialog.Destroy()

    def on_help(self, event):
        help_dialog = HelpDialog(self)
        help_dialog.ShowModal()
        help_dialog.Destroy()

    def on_close(self, event):
        self.Close()

class GlobalPlugin(globalPluginHandler.GlobalPlugin):
    def __init__(self):
        super(GlobalPlugin, self).__init__()
        if globalVars.appArgs.secure:
            return
        
        self.main_dialog = None
        self.create_menu()

    def create_menu(self):
        self.tools_menu = gui.mainFrame.sysTrayIcon.toolsMenu
        self.pdf_reader_item = self.tools_menu.Append(
            wx.ID_ANY,
            _("PDF &Reader"),
            _("Open PDF Reader")
        )
        gui.mainFrame.sysTrayIcon.Bind(
            wx.EVT_MENU,
            self.on_tools_menu_pdf_reader,
            self.pdf_reader_item
        )

    def on_tools_menu_pdf_reader(self, event):
        self.script_show_main_dialog(None)

    @script(
        description=_("Open PDF Reader"),
        category=_("PDF Reader"),
        gesture="kb:NVDA+alt+p"
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
        except:
            pass
