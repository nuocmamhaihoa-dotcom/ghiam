/*
 * Windows launcher for the local address book.
 *
 * The official 7-Zip SFX module extracts into a temporary folder, prefixes
 * RunProgram with ".\" and starts it with CreateProcess. It cannot start
 * cmd.exe or a .bat file from that folder, and it deletes the temporary
 * folder when the launched program exits. This program is a real exe at the
 * archive root. On first run it moves the app into %LOCALAPPDATA%\DanhBa and
 * starts that installed copy, which survives after the installer exits.
 */

#define WIN32_LEAN_AND_MEAN
#define _WIN32_WINNT 0x0601

#include <windows.h>
#include <objbase.h>
#include <shellapi.h>

#include <stdio.h>
#include <string.h>

static void show_error(const wchar_t *text) {
  wchar_t message[640];
  _snwprintf(message, 640, L"%s\nMã lỗi %lu", text, GetLastError());
  message[639] = L'\0';
  MessageBoxW(NULL, message, L"Danh bạ", MB_ICONERROR | MB_OK);
}

static void trim_slash(wchar_t *path) {
  size_t length = wcslen(path);
  while (length > 0 && (path[length - 1] == L'\\' || path[length - 1] == L'/')) {
    path[length - 1] = L'\0';
    length -= 1;
  }
}

static int join_path(wchar_t *out, size_t cap, const wchar_t *left, const wchar_t *right) {
  if (_snwprintf(out, cap, L"%s\\%s", left, right) < 0) {
    return 0;
  }
  out[cap - 1] = L'\0';
  return 1;
}

static int same_dir(const wchar_t *left, const wchar_t *right) {
  wchar_t full_left[1024];
  wchar_t full_right[1024];
  if (!GetFullPathNameW(left, 1024, full_left, NULL)) {
    return 0;
  }
  if (!GetFullPathNameW(right, 1024, full_right, NULL)) {
    return 0;
  }
  trim_slash(full_left);
  trim_slash(full_right);
  return CompareStringOrdinal(full_left, -1, full_right, -1, TRUE) == CSTR_EQUAL;
}

static int exists(const wchar_t *path) {
  return GetFileAttributesW(path) != INVALID_FILE_ATTRIBUTES;
}

static int delete_tree(const wchar_t *path) {
  wchar_t from[1026];
  SHFILEOPSTRUCTW op;
  size_t length = wcslen(path);
  if (!exists(path)) {
    return 1;
  }
  if (length + 2 >= 1024) {
    return 0;
  }
  memcpy(from, path, length * sizeof(wchar_t));
  from[length] = L'\0';
  from[length + 1] = L'\0';
  ZeroMemory(&op, sizeof(op));
  op.wFunc = FO_DELETE;
  op.pFrom = from;
  op.fFlags = FOF_NOCONFIRMATION | FOF_NOERRORUI | FOF_SILENT;
  return SHFileOperationW(&op) == 0 && !op.fAnyOperationsAborted;
}

static int move_dir(const wchar_t *source, const wchar_t *dest) {
  if (!exists(source)) {
    return 1;
  }
  if (exists(dest) && !delete_tree(dest)) {
    return 0;
  }
  return MoveFileExW(source, dest, MOVEFILE_COPY_ALLOWED | MOVEFILE_WRITE_THROUGH);
}

static void append_ps_string(FILE *file, const wchar_t *value) {
  char utf8[2048];
  int index;
  int bytes = WideCharToMultiByte(CP_UTF8, 0, value, -1, utf8, (int)sizeof(utf8), NULL, NULL);
  fputc('\'', file);
  if (bytes > 1) {
    for (index = 0; index < bytes - 1; index += 1) {
      unsigned char ch = (unsigned char)utf8[index];
      if (ch == '\'') {
        fputc('\'', file);
      }
      fputc((int)ch, file);
    }
  }
  fputc('\'', file);
}

static void create_shortcut(const wchar_t *dir, const wchar_t *exe) {
  wchar_t script[1024];
  wchar_t command[1400];
  FILE *file;
  STARTUPINFOW startup;
  PROCESS_INFORMATION process;
  if (!join_path(script, 1024, dir, L"tao-shortcut.ps1")) {
    return;
  }
  file = _wfopen(script, L"wb");
  if (!file) {
    return;
  }
  fputs("\xEF\xBB\xBF$w = New-Object -ComObject WScript.Shell\r\n", file);
  fputs("$s = $w.CreateShortcut($w.SpecialFolders('Desktop') + '\\Danh ba.lnk')\r\n", file);
  fputs("$s.TargetPath = ", file);
  append_ps_string(file, exe);
  fputs("\r\n$s.WorkingDirectory = ", file);
  append_ps_string(file, dir);
  fputs("\r\n$s.Description = 'Danh ba tren may tinh'\r\n$s.Save()\r\n", file);
  fclose(file);
  _snwprintf(command, 1400, L"powershell.exe -NoProfile -ExecutionPolicy Bypass -File \"%s\"", script);
  command[1399] = L'\0';
  ZeroMemory(&startup, sizeof(startup));
  startup.cb = sizeof(startup);
  startup.dwFlags = STARTF_USESHOWWINDOW;
  startup.wShowWindow = SW_HIDE;
  if (CreateProcessW(NULL, command, NULL, NULL, FALSE, CREATE_NO_WINDOW, NULL, dir, &startup, &process)) {
    WaitForSingleObject(process.hProcess, 20000);
    CloseHandle(process.hThread);
    CloseHandle(process.hProcess);
  }
}

static int run_python(const wchar_t *root) {
  wchar_t python_exe[1024];
  wchar_t script[1024];
  wchar_t kho[1024];
  wchar_t vendor[1024];
  wchar_t source[1024];
  wchar_t python_path[2048];
  wchar_t command[2400];
  STARTUPINFOW startup;
  PROCESS_INFORMATION process;
  DWORD code = 1;

  if (!join_path(python_exe, 1024, root, L"python\\python.exe") ||
      !join_path(script, 1024, root, L"contact_to_vcf\\src\\web\\pc_launcher.py") ||
      !join_path(kho, 1024, root, L"kho") ||
      !join_path(vendor, 1024, root, L"vendor") ||
      !join_path(source, 1024, root, L"contact_to_vcf\\src")) {
    show_error(L"Đường dẫn cài đặt quá dài.");
    return 1;
  }
  if (!exists(python_exe) || !exists(script) || !exists(kho)) {
    show_error(L"Bộ cài chưa đủ thư mục python, chương trình hoặc kho số.");
    return 1;
  }
  _snwprintf(python_path, 2048, L"%s;%s", vendor, source);
  python_path[2047] = L'\0';
  SetEnvironmentVariableW(L"DANHBA_LOCAL", L"1");
  SetEnvironmentVariableW(L"DANHBA_HOST", L"127.0.0.1");
  SetEnvironmentVariableW(L"DANHBA_PORT", L"8080");
  SetEnvironmentVariableW(L"DANHBA_KHO", kho);
  SetEnvironmentVariableW(L"PYTHONPATH", python_path);
  SetEnvironmentVariableW(L"PYTHONIOENCODING", L"utf-8");

  AllocConsole();
  SetConsoleTitleW(L"Danh bạ trên máy tính");
  SetConsoleOutputCP(CP_UTF8);
  SetConsoleCP(CP_UTF8);

  _snwprintf(command, 2400, L"\"%s\" \"%s\"", python_exe, script);
  command[2399] = L'\0';
  ZeroMemory(&startup, sizeof(startup));
  startup.cb = sizeof(startup);
  if (!CreateProcessW(NULL, command, NULL, NULL, TRUE, 0, NULL, root, &startup, &process)) {
    show_error(L"Không mở được Danh bạ trên máy tính này.");
    return 1;
  }
  WaitForSingleObject(process.hProcess, INFINITE);
  GetExitCodeProcess(process.hProcess, &code);
  CloseHandle(process.hThread);
  CloseHandle(process.hProcess);
  return (int)code;
}

static int install_from_temp(const wchar_t *source_root, const wchar_t *dest_root) {
  wchar_t source_exe[1024];
  wchar_t dest_exe[1024];
  wchar_t source_db[1024];
  wchar_t dest_db[1024];
  wchar_t source_child[1024];
  wchar_t dest_child[1024];
  const wchar_t *folders[] = {L"python", L"vendor", L"contact_to_vcf"};
  size_t index;
  STARTUPINFOW startup;
  PROCESS_INFORMATION process;
  wchar_t command[1200];

  if (!CreateDirectoryW(dest_root, NULL) && GetLastError() != ERROR_ALREADY_EXISTS) {
    show_error(L"Không tạo được thư mục Danh bạ trong máy tính.");
    return 1;
  }
  if (!join_path(source_exe, 1024, source_root, L"Mo-Danh-Ba.exe") ||
      !join_path(dest_exe, 1024, dest_root, L"Mo-Danh-Ba.exe")) {
    show_error(L"Đường dẫn cài đặt quá dài.");
    return 1;
  }
  if (!CopyFileW(source_exe, dest_exe, FALSE)) {
    show_error(L"Không chép được chương trình vào máy tính.");
    return 1;
  }
  for (index = 0; index < 3; index += 1) {
    if (!join_path(source_child, 1024, source_root, folders[index]) ||
        !join_path(dest_child, 1024, dest_root, folders[index]) ||
        !move_dir(source_child, dest_child)) {
      show_error(L"Không đưa được phần mềm vào máy tính.");
      return 1;
    }
  }
  if (!join_path(source_db, 1024, source_root, L"kho\\kho.sqlite") ||
      !join_path(dest_db, 1024, dest_root, L"kho\\kho.sqlite")) {
    show_error(L"Đường dẫn kho số quá dài.");
    return 1;
  }
  if (!exists(dest_db)) {
    if (!join_path(source_child, 1024, source_root, L"kho") ||
        !join_path(dest_child, 1024, dest_root, L"kho") ||
        !move_dir(source_child, dest_child)) {
      show_error(L"Không đưa được kho số vào máy tính.");
      return 1;
    }
  }
  if (!join_path(source_child, 1024, source_root, L"Mo-Danh-Ba.bat") ||
      !join_path(dest_child, 1024, dest_root, L"Mo-Danh-Ba.bat")) {
    return 1;
  }
  CopyFileW(source_child, dest_child, FALSE);
  if (!join_path(source_child, 1024, source_root, L"HUONG_DAN.txt") ||
      !join_path(dest_child, 1024, dest_root, L"HUONG_DAN.txt")) {
    return 1;
  }
  CopyFileW(source_child, dest_child, FALSE);
  create_shortcut(dest_root, dest_exe);

  _snwprintf(command, 1200, L"\"%s\"", dest_exe);
  command[1199] = L'\0';
  ZeroMemory(&startup, sizeof(startup));
  startup.cb = sizeof(startup);
  if (!CreateProcessW(dest_exe, command, NULL, NULL, FALSE, 0, NULL, dest_root, &startup, &process)) {
    show_error(L"Đã chép Danh bạ vào máy nhưng chưa mở được.");
    return 1;
  }
  CloseHandle(process.hThread);
  CloseHandle(process.hProcess);
  return 0;
}

static int install_dir(wchar_t *out, size_t cap) {
  wchar_t base[1024];
  DWORD length = GetEnvironmentVariableW(L"LOCALAPPDATA", base, 1024);
  if (length == 0 || length >= 1024) {
    length = GetEnvironmentVariableW(L"USERPROFILE", base, 1024);
    if (length == 0 || length >= 1024) {
      return 0;
    }
    trim_slash(base);
    return _snwprintf(out, cap, L"%s\\AppData\\Local\\DanhBa", base) > 0;
  }
  trim_slash(base);
  return _snwprintf(out, cap, L"%s\\DanhBa", base) > 0;
}

int WINAPI wWinMain(HINSTANCE instance, HINSTANCE previous, PWSTR command, int show) {
  wchar_t exe_path[1024];
  wchar_t folder[1024];
  wchar_t *file_name;
  wchar_t dest[1024];
  (void)instance;
  (void)previous;
  (void)command;
  (void)show;
  CoInitializeEx(NULL, COINIT_APARTMENTTHREADED);
  if (!GetModuleFileNameW(NULL, exe_path, 1023)) {
    show_error(L"Không đọc được vị trí chương trình.");
    return 1;
  }
  exe_path[1023] = L'\0';
  if (wcslen(exe_path) >= 1024) {
    show_error(L"Đường dẫn chương trình quá dài.");
    return 1;
  }
  wcscpy(folder, exe_path);
  file_name = wcsrchr(folder, L'\\');
  if (!file_name) {
    show_error(L"Không đọc được thư mục chương trình.");
    return 1;
  }
  *file_name = L'\0';
  if (!install_dir(dest, 1024)) {
    show_error(L"Không thấy thư mục người dùng trên máy tính này.");
    return 1;
  }
  if (same_dir(folder, dest)) {
    create_shortcut(dest, exe_path);
    return run_python(dest);
  }
  return install_from_temp(folder, dest);
}
