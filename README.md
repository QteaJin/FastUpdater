# FastUpdater

A small Windows app that updates your installed programs in a few clicks. It shows every program that has a newer version available (using Windows' built-in `winget`) and lets you update them one by one, selected, or all at once.

No dependencies, no installer, no administrator rights needed.

## What you need

- Windows 10 or 11 with **winget** (App Installer). It is already included in modern Windows. If it is missing, install "App Installer" from the Microsoft Store.
- **Python 3** with Tkinter.

## How to install and run

1. Install Python: [python-3.13.16-amd64.exe](https://www.python.org/ftp/python/3.13.16/python-3.13.16-amd64.exe). Keep the default options (make sure "tcl/tk and IDLE" stays checked).
2. Download or clone this repository.
3. Double-click **`run.bat`**.

That's it. If Python is not found, `run.bat` will tell you. It never installs anything by itself.

## How to use

1. The list of available updates loads automatically on start. Press **Check for updates** to refresh it.
2. Tick the programs you want (click the box on the left, or press Space on a row), then press **Update selected**.
3. Or press **Update all** to update everything except excluded programs and programs that need administrator rights.
4. Watch the **Status** column. When it finishes, the list refreshes by itself.

Other buttons:

- **EN / RU** (top right): switch the interface language between English and Russian. Your choice is remembered.
- **Theme**: switch between light and dark.
- **Open log**: open today's log file.

## Settings

`config.json` is created next to the app on first start:

| Option | Meaning |
| --- | --- |
| `exclude` | Package IDs that are never shown or updated (wildcards like `*` work). |
| `admin_required` | Package IDs marked "admin required": not pre-selected and skipped by **Update all**. |
| `include_admin_in_update_all` | Set to `true` to include admin-required programs in **Update all**. |
| `language` | `en` (default) or `ru`. |

If an update fails because it needs administrator rights, the program remembers it (`learned_admin.json`) and marks it in advance next time. To update such a program, run the app as administrator and select it manually.

## Logs

Logs are saved to `%USERPROFILE%\UpdateLogs\`:

- `update-YYYY-MM-DD.log`: a readable log for each day.
- `failures.jsonl`: details of every failed update, handy for troubleshooting.

## Troubleshooting

- **"winget not found"**: install "App Installer" from the Microsoft Store.
- **Status "may be running, close it and retry"**: close that program and update again.
- **Parser check**: `py -3 updater.py --test-parser sample_output.txt`
