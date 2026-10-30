import os
import pwd


def main() -> None:
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        os.makedirs("/var/lib/coderd/git", exist_ok=True)
        account = pwd.getpwnam("coder")
        for dirpath, dirnames, filenames in os.walk("/var/lib/coderd"):
            os.chown(dirpath, account.pw_uid, account.pw_gid)
            for name in dirnames + filenames:
                os.chown(os.path.join(dirpath, name), account.pw_uid, account.pw_gid)
        os.setgid(account.pw_gid)
        os.setuid(account.pw_uid)
    os.execvp(
        "uvicorn",
        ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"],
    )


if __name__ == "__main__":
    main()
