# Hướng dẫn sử dụng CortexHarness Context Menu Installer

## 🎯 Cách sử dụng đơn giản (KHÔNG cần Inno Setup)

### Cài đặt Context Menu cho Windows

```bash
# Cài đặt cho user hiện tại (không cần admin)
dev installer install --local

# Xóa cài đặt
dev installer uninstall --local
```

### Những gì sẽ được cài đặt?

Khi chạy `dev installer install --local`:

1. **Registry Entries**: Tạo menu "CortexHarness" trong Windows Explorer context menu
2. **Script Wrapper**: Copy file wrapper.bat vào thư mục home
3. **Commands**: 3 lệnh mặc định:
   - Sync Code
   - Sync Documents  
   - Run Harness

### Sau khi cài đặt?

- Chuột phải vào bất kỳ folder nào trong Windows Explorer
- Chọn "CortexHarness" từ context menu
- Chọn lệnh muốn thực hiện (Sync Code, Sync Documents, etc.)
- CortexHarness CLI sẽ chạy với folder path được truyền tự động

## 🔧 Chi tiết kỹ thuật

### Files được tạo/sửa đổi

**Registry Keys** (HKCU cho local install):
```
HKEY_CURRENT_USER\Software\Classes\Directory\shell\CortexHarness
HKEY_CURRENT_USER\Software\Classes\Directory\Background\shell\CortexHarness
```

**Files được copy**:
```
%USERPROFILE%\CortexHarness\scripts\wrapper.bat
```

### Commands có sẵn

```json
{
  "menu_name": "CortexHarness",
  "commands": [
    {"name": "Sync Code", "action": "sync code"},
    {"name": "Sync Documents", "action": "sync doc"},
    {"name": "Run Harness", "action": "harness run"}
  ]
}
```

## 🚀 Tùy chỉnh Commands

### Thêm command mới

```python
# Sử dụng Python CLI
python -m installers.common.config_manager add \
    "New Command" \
    "sync code --force" \
    "Force sync code changes"
```

### Xóa command

```python
python -m installers.common.config_manager remove "New Command"
```

## 🔨 Advanced: Build production installers

### Windows .exe installer (Optional)

**Yêu cầu**: Inno Setup compiler

1. Download: https://jrsoftware.org/isdl.php
2. Install vào PATH
3. Chạy:
   ```bash
   dev installer build --platform windows
   ```
4. Output: `dist/cortex-harness-setup.exe`

### macOS .pkg installer (Chỉ trên macOS)

```bash
dev installer build --platform macos
# Output: dist/cortex-harness-macos.pkg
```

### Ubuntu .deb package (Chỉ trên Ubuntu)

```bash
dev installer build --platform ubuntu  
# Output: dist/cortex-harness-contextmenu_1.0.0_all.deb
```

## 🐛 Troubleshooting

### "Inno Setup compiler not found"
- **Normal**: Không ảnh hưởng chức năng chính
- **Fix**: Sử dụng `--local` option thay vì build installer

### Context menu không xuất hiện
1. Restart Windows Explorer:
   ```cmd
   taskkill /f /im explorer.exe && start explorer.exe
   ```
2. Check Registry:
   ```cmd
   reg query HKCU\Software\Classes\Directory\shell\CortexHarness
   ```

### Lệnh không chạy được
1. Check file wrapper.bat có tồn tại:
   ```cmd
   dir %USERPROFILE%\CortexHarness\scripts\wrapper.bat
   ```
2. Test trực tiếp:
   ```cmd
   %USERPROFILE%\CortexHarness\scripts\wrapper.bat "sync code" "C:\test\folder"
   ```

## 📝 Summary

**Development**: `dev installer install --local` ✅ (KHÔNG cần external tools)

**Production**: 
- Windows: Build .exe với Inno Setup (optional)
- macOS: Build .pkg trên macOS (optional)  
- Ubuntu: Build .deb trên Ubuntu (optional)

**Uninstall**: `dev installer uninstall --local` ✅