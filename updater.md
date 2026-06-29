# FinanceBook — Release & Update Cheat Sheet

A plain-English guide to working on the app and shipping updates.
There are only **two activities**: working on the code, and releasing a version.

---

## 1. Everyday — working on the code

- Your working folder: **`C:\Users\amazl\FinanceBook`**
- To run/test your changes, open PowerShell in that folder and run:
  ```powershell
  .\run.bat
  ```
- This only affects your own machine. Nobody else sees it until you release.

**On a different computer?** Install Python 3.12 first, then get the code once with:
```powershell
git clone https://github.com/Adeen924/FinanceBook.git
cd FinanceBook
.\run.bat
```

---

## 2. Releasing a new version to everyone

Open PowerShell in the repo folder and run these commands. **Pick the next
version number** in the `tag` line — that number becomes the app's version.

```powershell
cd C:\Users\amazl\FinanceBook
git add -A
git commit -m "describe what changed"
git push
git tag v1.0.2
git push origin v1.0.2
```

That's it. GitHub builds the app in the cloud and publishes the release
automatically. Watch the repo's **Actions** tab — a green checkmark means it's
live and users will be offered the update.

**Rules of thumb:**
- The version number always goes **up**. Never reuse one.
  - Small fix: `v1.0.1` → `v1.0.2`
  - New feature: `v1.0.2` → `v1.1.0`
- You do **NOT** edit a version number anywhere in the code — the tag sets it.
- You do **NOT** run `build.bat` to release — GitHub does the building for you.

---

## 3. Giving the app to a user the first time

Send them this link:

> **https://github.com/Adeen924/FinanceBook/releases**

They click the latest release and download the **big** file, then extract it
anywhere and double-click **`FinanceBook.exe`**. No Python, no install.

On first launch Windows shows **"Windows protected your PC"** (because the app
isn't code-signed) → click **More info** → **Run anyway**. One-time only.

**Two files appear on each release — which is which:**
| File | Who uses it |
|---|---|
| `FinanceBook-x.y.z.zip` | **You give people this one** — the full app |
| `app-x.y.z.zip` | Ignore — the auto-updater downloads this itself |

---

## 4. How existing users get updates

You do nothing extra after releasing. The next time they open FinanceBook:
1. It checks GitHub for a newer version (on startup, in the background).
2. If there's one, it shows an **"Update available"** banner.
3. They click **Install Update** once → it downloads, restarts, done.

Their data is never touched — it lives separately at
`%APPDATA%\FinanceBook\finances.db`.

---

## 5. Optional — test a build on your own PC before releasing

Only if you want to try the packaged `.exe` locally before shipping:
```powershell
.\build.bat
```
This creates a `dist\` folder with `FinanceBook.exe` and the zip files. Run
`dist\FinanceBook\FinanceBook.exe` to test. (This step is for your own testing
only — it is NOT part of releasing.)
