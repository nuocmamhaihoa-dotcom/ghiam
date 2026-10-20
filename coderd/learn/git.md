# Git trên máy này

Workspace là một repo nhánh `main`.
Git trên VPS là bản cũ, không có `git init -b`.

Sau khi sửa file, coderd tự commit và đẩy bundle lên VPS.
Người dùng không cần gõ git cho việc đó.

Xem thay đổi: `git status --porcelain`.
Xem lịch sử: `git log --oneline -n 5`.
