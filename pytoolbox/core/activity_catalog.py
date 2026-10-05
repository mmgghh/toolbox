"""Built-in knowledge of common apps and sites for ``pytime auto``.

One entry per app: what it is, every window class it's known to report, and
the suffix it appends to window titles. Classes are lowercase and cover the
X11 ``WM_CLASS``, the Wayland app id and the Flatpak id where they differ; an
id that turns out to be wrong only means that entry never matches, so they
are listed generously. ``pytime auto probe`` shows the real class of any
window, and ``~/.pytime/rules.json`` adds to (never replaces) this catalog.

Kinds:

- ``editor``   titles parsed for a file and a project
- ``terminal`` the running program and directory read from /proc
- ``browser``  tab titles parsed for a site or repository
- ``app``      recorded under the app's name; ``project`` attributes all of
               its time to one project, for apps whose titles say nothing
               about which of your projects the work was for (chat, AI, mail)
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class App:
    name: str
    kind: str
    classes: tuple[str, ...]
    project: str = ""
    suffixes: tuple[str, ...] = ()
    #: Password managers and the like: time counts, nothing from the title is kept.
    redact: bool = False


def _app(name, kind, *classes, project="", suffixes=(), redact=False) -> App:
    return App(name, kind, tuple(classes), project, tuple(suffixes), redact)


APPS: tuple[App, ...] = (
    # --- Editors and IDEs --------------------------------------------------
    _app("VS Code", "editor", "code", "code-url-handler", "com.visualstudio.code", "visual-studio-code",
         suffixes=("visual studio code",)),
    _app("VS Code Insiders", "editor", "code - insiders", "code-insiders",
         suffixes=("visual studio code - insiders",)),
    _app("VS Code (OSS)", "editor", "code - oss", "code-oss", "com.visualstudio.code-oss", suffixes=("code - oss",)),
    _app("VSCodium", "editor", "codium", "vscodium", "com.vscodium.codium", suffixes=("vscodium",)),
    _app("Cursor", "editor", "cursor", "cursor-url-handler", suffixes=("cursor",)),
    _app("Windsurf", "editor", "windsurf", suffixes=("windsurf",)),
    _app("Zed", "editor", "zed", "dev.zed.zed", "dev.zed.zed-preview", suffixes=("zed",)),
    _app("PyCharm", "editor", "jetbrains-pycharm", "pycharm",
         suffixes=("pycharm professional edition", "pycharm")),
    _app("PyCharm CE", "editor", "jetbrains-pycharm-ce", "com.jetbrains.pycharm-community",
         suffixes=("pycharm community edition",)),
    _app("IntelliJ IDEA", "editor", "jetbrains-idea", "idea", "com.jetbrains.intellij-idea-ultimate",
         suffixes=("intellij idea ultimate edition", "intellij idea")),
    _app("IntelliJ IDEA CE", "editor", "jetbrains-idea-ce", "com.jetbrains.intellij-idea-community",
         suffixes=("intellij idea community edition",)),
    _app("WebStorm", "editor", "jetbrains-webstorm", "webstorm", suffixes=("webstorm",)),
    _app("GoLand", "editor", "jetbrains-goland", "goland", suffixes=("goland",)),
    _app("CLion", "editor", "jetbrains-clion", "clion", suffixes=("clion",)),
    _app("Rider", "editor", "jetbrains-rider", "rider", suffixes=("rider",)),
    _app("PhpStorm", "editor", "jetbrains-phpstorm", "phpstorm", suffixes=("phpstorm",)),
    _app("RubyMine", "editor", "jetbrains-rubymine", "rubymine", suffixes=("rubymine",)),
    _app("RustRover", "editor", "jetbrains-rustrover", "rustrover", suffixes=("rustrover",)),
    _app("DataGrip", "editor", "jetbrains-datagrip", "datagrip", suffixes=("datagrip",)),
    _app("DataSpell", "editor", "jetbrains-dataspell", "dataspell", suffixes=("dataspell",)),
    _app("Android Studio", "editor", "jetbrains-studio", "android-studio", "com.google.androidstudio",
         suffixes=("android studio",)),
    _app("Sublime Text", "editor", "sublime_text", "sublime-text", "com.sublimetext.three",
         suffixes=("sublime text",)),
    _app("Kate", "editor", "kate", "org.kde.kate", suffixes=("kate",)),
    _app("KWrite", "editor", "kwrite", "org.kde.kwrite", suffixes=("kwrite",)),
    _app("Text Editor", "editor", "org.gnome.texteditor", "gnome-text-editor", suffixes=("text editor",)),
    _app("gedit", "editor", "gedit", "org.gnome.gedit", suffixes=("gedit",)),
    _app("Geany", "editor", "geany", "org.geany.geany", suffixes=("geany",)),
    _app("Emacs", "editor", "emacs", "org.gnu.emacs"),
    _app("Vim", "editor", "vim", "gvim"),
    _app("Neovim", "editor", "nvim", "neovide", "neovim-qt", "nvim-qt"),
    _app("Lapce", "editor", "lapce", "dev.lapce.lapce", suffixes=("lapce",)),
    _app("Helix", "editor", "helix"),
    _app("Eclipse", "editor", "eclipse", "org.eclipse.java", suffixes=("eclipse ide", "eclipse")),
    _app("NetBeans", "editor", "apache netbeans", "netbeans", "org.apache.netbeans", suffixes=("apache netbeans",)),
    _app("Qt Creator", "editor", "qtcreator", "org.qt-project.qtcreator", suffixes=("qt creator",)),
    _app("KDevelop", "editor", "kdevelop", "org.kde.kdevelop", suffixes=("kdevelop",)),
    _app("GNOME Builder", "editor", "org.gnome.builder", "gnome-builder", suffixes=("builder",)),
    _app("Spyder", "editor", "spyder", "org.spyder_ide.spyder"),
    _app("RStudio", "editor", "rstudio", suffixes=("rstudio",)),
    _app("Arduino IDE", "editor", "arduino ide", "arduino-ide", "cc.arduino.ide2"),
    _app("Godot", "editor", "godot", "org.godotengine.godot", suffixes=("godot engine",)),
    _app("Unity", "editor", "unity", "unityhub"),

    # --- Terminals -----------------------------------------------------------
    _app("Terminal", "terminal", "gnome-terminal-server", "gnome-terminal", "org.gnome.terminal"),
    _app("Console", "terminal", "org.gnome.console", "kgx"),
    _app("Ptyxis", "terminal", "org.gnome.ptyxis", "ptyxis", "app.devsuite.ptyxis"),
    _app("Konsole", "terminal", "konsole", "org.kde.konsole"),
    _app("Yakuake", "terminal", "yakuake", "org.kde.yakuake"),
    _app("Kitty", "terminal", "kitty"),
    _app("Alacritty", "terminal", "alacritty", "org.alacritty.alacritty"),
    _app("WezTerm", "terminal", "wezterm", "org.wezfurlong.wezterm", "wezterm-gui"),
    _app("Ghostty", "terminal", "ghostty", "com.mitchellh.ghostty"),
    _app("Foot", "terminal", "foot", "footclient"),
    _app("Tilix", "terminal", "tilix", "com.gexperts.tilix"),
    _app("Terminator", "terminal", "terminator"),
    _app("Guake", "terminal", "guake"),
    _app("Tilda", "terminal", "tilda"),
    _app("Black Box", "terminal", "com.raggesilver.blackbox", "blackbox"),
    _app("Warp", "terminal", "dev.warp.warp", "warp", "warp-terminal"),
    _app("Tabby", "terminal", "tabby"),
    _app("Hyper", "terminal", "hyper"),
    _app("Wave", "terminal", "waveterm", "wave"),
    _app("Rio", "terminal", "rio"),
    _app("Contour", "terminal", "contour", "org.contourterminal.contour"),
    _app("Xfce Terminal", "terminal", "xfce4-terminal"),
    _app("MATE Terminal", "terminal", "mate-terminal"),
    _app("LXTerminal", "terminal", "lxterminal"),
    _app("QTerminal", "terminal", "qterminal"),
    _app("Deepin Terminal", "terminal", "deepin-terminal"),
    _app("Terminology", "terminal", "terminology"),
    _app("Sakura", "terminal", "sakura"),
    _app("cool-retro-term", "terminal", "cool-retro-term"),
    _app("XTerm", "terminal", "xterm", "uxterm"),
    _app("urxvt", "terminal", "urxvt", "urxvt256c", "rxvt"),
    _app("st", "terminal", "st", "st-256color"),

    # --- Browsers ------------------------------------------------------------
    _app("Chrome", "browser", "google-chrome", "google-chrome-beta", "google-chrome-unstable",
         "com.google.chrome", suffixes=("google chrome",)),
    _app("Chromium", "browser", "chromium", "chromium-browser", "org.chromium.chromium", suffixes=("chromium",)),
    _app("Firefox", "browser", "firefox", "firefox-esr", "org.mozilla.firefox", "firefox_firefox", "navigator",
         suffixes=("mozilla firefox", "firefox")),
    _app("Firefox Developer Edition", "browser", "firefoxdeveloperedition", "firefox-developer-edition",
         "firefox-aurora", suffixes=("firefox developer edition",)),
    _app("Firefox Nightly", "browser", "firefox-nightly", "org.mozilla.firefox.nightly", suffixes=("firefox nightly",)),
    _app("LibreWolf", "browser", "librewolf", "io.gitlab.librewolf-community", suffixes=("librewolf",)),
    _app("Floorp", "browser", "floorp", "one.ablaze.floorp", suffixes=("floorp",)),
    _app("Zen", "browser", "zen", "zen-alpha", "zen-browser", "app.zen_browser.zen", suffixes=("zen browser", "zen")),
    _app("Waterfox", "browser", "waterfox", suffixes=("waterfox",)),
    _app("Mullvad Browser", "browser", "mullvad browser", "mullvad-browser", suffixes=("mullvad browser",)),
    _app("Tor Browser", "browser", "tor browser", "torbrowser", suffixes=("tor browser",)),
    _app("Brave", "browser", "brave-browser", "brave-browser-beta", "com.brave.browser", suffixes=("brave",)),
    _app("Edge", "browser", "microsoft-edge", "microsoft-edge-beta", "microsoft-edge-dev", "com.microsoft.edge",
         suffixes=("microsoft edge",)),
    _app("Opera", "browser", "opera", "com.opera.opera", suffixes=("opera",)),
    _app("Vivaldi", "browser", "vivaldi-stable", "vivaldi", "com.vivaldi.vivaldi", suffixes=("vivaldi",)),
    _app("Thorium", "browser", "thorium-browser", suffixes=("thorium",)),
    _app("qutebrowser", "browser", "qutebrowser", "org.qutebrowser.qutebrowser", suffixes=("qutebrowser",)),
    _app("Falkon", "browser", "falkon", "org.kde.falkon", suffixes=("falkon",)),
    _app("GNOME Web", "browser", "epiphany", "org.gnome.epiphany"),

    # --- AI assistants (their titles never say which project) ----------------
    _app("Claude", "app", "com.anthropic.claude", "claude", "claude-desktop", project="Claude"),
    _app("ChatGPT", "app", "chatgpt", "com.openai.chatgpt", project="ChatGPT"),
    _app("Jan", "app", "jan", "ai.jan.jan", project="Jan"),
    _app("LM Studio", "app", "lm studio", "lm-studio", project="LM Studio"),

    # --- Chat, meetings, mail ------------------------------------------------
    _app("Slack", "app", "slack", "com.slack.slack", project="Slack"),
    _app("Discord", "app", "discord", "com.discordapp.discord", "vesktop", "dev.vencord.vesktop", "webcord",
         project="Discord"),
    _app("Telegram", "app", "telegramdesktop", "telegram-desktop", "org.telegram.desktop", project="Telegram"),
    _app("Signal", "app", "signal", "signal desktop", "org.signal.signal", project="Signal"),
    _app("Element", "app", "element", "im.riot.riot", project="Element"),
    _app("Mattermost", "app", "mattermost", "com.mattermost.desktop", project="Mattermost"),
    _app("Zulip", "app", "zulip", "org.zulip.zulip", project="Zulip"),
    _app("Rocket.Chat", "app", "rocket.chat", "chat.rocket.rocketchat", project="Rocket.Chat"),
    _app("Teams", "app", "teams-for-linux", "microsoft teams - preview", "teams", project="Teams"),
    _app("Zoom", "app", "zoom", "us.zoom.zoom", project="Zoom"),
    _app("Skype", "app", "skype", "com.skype.client", project="Skype"),
    _app("Thunderbird", "app", "thunderbird", "thunderbird-esr", "org.mozilla.thunderbird", "net.thunderbird.thunderbird",
         project="Mail"),
    _app("Evolution", "app", "evolution", "org.gnome.evolution", project="Mail"),
    _app("Geary", "app", "geary", "org.gnome.geary", project="Mail"),
    _app("KMail", "app", "kmail", "org.kde.kmail2", project="Mail"),
    _app("Calendar", "app", "org.gnome.calendar", "gnome-calendar", project="Calendar"),

    # --- Notes and knowledge -------------------------------------------------
    _app("Obsidian", "app", "obsidian", "md.obsidian.obsidian", project="Obsidian"),
    _app("Notion", "app", "notion", "notion-app", "notion-app-enhanced", project="Notion"),
    _app("Logseq", "app", "logseq", "com.logseq.logseq", project="Logseq"),
    _app("Joplin", "app", "joplin", "net.cozic.joplin_desktop", "@joplin/app-desktop", project="Joplin"),
    _app("Anytype", "app", "anytype", "io.anytype.anytype", project="Anytype"),
    _app("AppFlowy", "app", "appflowy", "io.appflowy.appflowy", project="AppFlowy"),
    _app("Zotero", "app", "zotero", "org.zotero.zotero", project="Zotero"),

    # --- Developer tools (time shows under the app; project unknown) ---------
    _app("Postman", "app", "postman", "com.getpostman.postman"),
    _app("Insomnia", "app", "insomnia", "rest.insomnia.insomnia"),
    _app("Bruno", "app", "bruno", "com.usebruno.bruno"),
    _app("Hoppscotch", "app", "hoppscotch", "io.hoppscotch.desktop"),
    _app("DBeaver", "app", "dbeaver", "io.dbeaver.dbeavercommunity", "dbeaver-ce"),
    _app("pgAdmin", "app", "pgadmin4", "pgadmin 4", "org.pgadmin.pgadmin4"),
    _app("Beekeeper Studio", "app", "beekeeper studio", "beekeeper-studio", "io.beekeeperstudio.studio"),
    _app("DB Browser for SQLite", "app", "sqlitebrowser", "db browser for sqlite", "org.sqlitebrowser.sqlitebrowser"),
    _app("MySQL Workbench", "app", "mysql-workbench", "mysql-workbench-bin", "mysql workbench"),
    _app("MongoDB Compass", "app", "mongodb compass", "mongodb-compass"),
    _app("Redis Insight", "app", "redisinsight", "redis insight", "redis-insight"),
    _app("TablePlus", "app", "tableplus"),
    _app("Docker Desktop", "app", "docker desktop", "docker-desktop"),
    _app("Podman Desktop", "app", "podman desktop", "io.podman_desktop.podmandesktop"),
    _app("Lens", "app", "lens", "openlens", "freelens", "dev.k8slens.openlens"),
    _app("GitKraken", "app", "gitkraken", "com.axosoft.gitkraken"),
    _app("GitHub Desktop", "app", "github desktop", "github-desktop", "io.github.shiftey.desktop"),
    _app("Sublime Merge", "app", "sublime_merge", "sublime-merge", "com.sublimemerge.app"),
    _app("gitg", "app", "gitg", "org.gnome.gitg"),
    _app("Git Cola", "app", "git-cola", "com.github.git_cola.git-cola"),
    _app("Meld", "app", "meld", "org.gnome.meld"),
    _app("Wireshark", "app", "wireshark", "org.wireshark.wireshark"),
    _app("VirtualBox", "app", "virtualbox", "virtualbox manager", "virtualboxvm"),
    _app("Virtual Machine Manager", "app", "virt-manager", "org.virt_manager.virt-manager"),
    _app("Boxes", "app", "org.gnome.boxes", "gnome-boxes"),
    _app("Remmina", "app", "remmina", "org.remmina.remmina"),
    _app("FileZilla", "app", "filezilla", "org.filezillaproject.filezilla"),
    _app("Android Emulator", "app", "emulator", "qemu-system-x86_64"),

    # --- Design and media ------------------------------------------------------
    _app("Figma", "app", "figma-linux", "io.github.figma_linux.figma_linux"),
    _app("Inkscape", "app", "inkscape", "org.inkscape.inkscape"),
    _app("GIMP", "app", "gimp", "gimp-2.10", "org.gimp.gimp"),
    _app("Krita", "app", "krita", "org.kde.krita"),
    _app("Blender", "app", "blender", "org.blender.blender"),
    _app("Spotify", "app", "spotify", "com.spotify.client", project="Spotify"),

    # --- Office and files ------------------------------------------------------
    _app("LibreOffice Writer", "app", "libreoffice-writer"),
    _app("LibreOffice Calc", "app", "libreoffice-calc"),
    _app("LibreOffice Impress", "app", "libreoffice-impress"),
    _app("LibreOffice", "app", "libreoffice-startcenter", "soffice", "org.libreoffice.libreoffice"),
    _app("Document Viewer", "app", "evince", "org.gnome.evince", "org.gnome.papers", "papers"),
    _app("Okular", "app", "okular", "org.kde.okular"),
    _app("Files", "app", "org.gnome.nautilus", "nautilus"),
    _app("Dolphin", "app", "dolphin", "org.kde.dolphin"),
    _app("Thunar", "app", "thunar"),
    _app("Nemo", "app", "nemo"),

    # --- Password managers: counted, never described ---------------------------
    _app("KeePassXC", "app", "keepassxc", "org.keepassxc.keepassxc", redact=True),
    _app("Bitwarden", "app", "bitwarden", "com.bitwarden.desktop", redact=True),
    _app("1Password", "app", "1password", redact=True),
    _app("Proton Pass", "app", "proton pass", "proton-pass", redact=True),
    _app("Passwords and Keys", "app", "seahorse", "org.gnome.seahorse.application", redact=True),
)


#: Whole-word, case-insensitive match against a browser tab's title ->
#: project. First match wins. GitHub, GitLab and Jira tabs are attributed to
#: their repository or project key before this table is consulted. Words
#: that are too common to identify a site on their own ("linear", "meet",
#: "zoom", "medium", "copilot") are left out on purpose.
SITES: dict[str, str] = {
    # AI assistants
    "chatgpt": "ChatGPT",
    "claude": "Claude",
    "gemini": "Gemini",
    "perplexity": "Perplexity",
    "deepseek": "DeepSeek",
    "grok": "Grok",
    "hugging face": "Hugging Face",
    # Code hosting and review
    "github": "GitHub",
    "gitlab": "GitLab",
    "bitbucket": "Bitbucket",
    "gitea": "Gitea",
    "codeberg": "Codeberg",
    "sourcegraph": "Sourcegraph",
    # Q&A and documentation
    "stack overflow": "Stack Overflow",
    "stack exchange": "Stack Exchange",
    "mdn": "MDN",
    "devdocs": "DevDocs",
    "read the docs": "Read the Docs",
    "go packages": "Go Packages",
    "pypi": "PyPI",
    "npm": "npm",
    "crates.io": "crates.io",
    "rubygems": "RubyGems",
    "docker hub": "Docker Hub",
    # Cloud, CI and monitoring
    "google cloud": "Google Cloud",
    "azure": "Azure",
    "vercel": "Vercel",
    "netlify": "Netlify",
    "cloudflare": "Cloudflare",
    "digitalocean": "DigitalOcean",
    "heroku": "Heroku",
    "supabase": "Supabase",
    "firebase": "Firebase",
    "sentry": "Sentry",
    "grafana": "Grafana",
    "datadog": "Datadog",
    "kibana": "Kibana",
    "jenkins": "Jenkins",
    "circleci": "CircleCI",
    "portainer": "Portainer",
    # Notebooks and data
    "jupyterlab": "Jupyter",
    "jupyter notebook": "Jupyter",
    "colab": "Colab",
    "kaggle": "Kaggle",
    # Planning, docs and design
    "jira": "Jira",
    "confluence": "Confluence",
    "notion": "Notion",
    "trello": "Trello",
    "clickup": "ClickUp",
    "linear.app": "Linear",
    "excalidraw": "Excalidraw",
    "miro": "Miro",
    "figma": "Figma",
    "canva": "Canva",
    "google docs": "Google Docs",
    "google sheets": "Google Sheets",
    "google slides": "Google Slides",
    "google drive": "Google Drive",
    "google calendar": "Google Calendar",
    # Communication
    "gmail": "Gmail",
    "outlook": "Outlook",
    "slack": "Slack",
    "discord": "Discord",
    "microsoft teams": "Teams",
    "whatsapp": "WhatsApp",
    "telegram": "Telegram",
    # Learning, reading, social
    "youtube": "YouTube",
    "wikipedia": "Wikipedia",
    "dev community": "DEV",
    "hacker news": "Hacker News",
    "reddit": "Reddit",
    "linkedin": "LinkedIn",
    "leetcode": "LeetCode",
    "hackerrank": "HackerRank",
    "coursera": "Coursera",
    "udemy": "Udemy",
}


def classes_of(kind: str) -> set[str]:
    return {cls for app in APPS if app.kind == kind for cls in app.classes}


def labels() -> dict[str, str]:
    return {cls: app.name for app in APPS for cls in app.classes}


def app_projects() -> dict[str, str]:
    return {cls: app.project for app in APPS if app.project for cls in app.classes}


def redacted_classes() -> set[str]:
    return {cls for app in APPS if app.redact for cls in app.classes}


def title_suffixes() -> tuple[str, ...]:
    """Every app-name suffix, longest first so "firefox developer edition" beats "firefox"."""
    suffixes = {suffix for app in APPS for suffix in app.suffixes}
    return tuple(sorted(suffixes, key=len, reverse=True))
