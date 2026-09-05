; ============================================================
; invest-concierge Inno Setup 安装器脚本（SHELL_UPGRADE_PLAN §1）
; 版本：与 server/main.py FastAPI(version=) 对齐，由构建脚本 -D 注入或此处手改
; 构建：scripts/build_installer.bat（自动探测 ISCC 路径）
;
; 设计要点：
; - 主 exe 是 onefile（已含 Python/依赖/前端 dist），安装器只做搬运+注册
; - 数据落 %LOCALAPPDATA%\invest-concierge：快捷方式「起始位置」指过去，
;   exe 的 SQLite(DB_FILE) 是 CWD 相对路径 → Program Files 只读也安全
; - 卸载询问是否保留用户数据（持仓/日记/会话），默认保留
; - 无代码签名（开源免费版）；SmartScreen 首次提示属预期，release notes 说明
; ============================================================

#define MyAppName "invest-concierge"
#define MyAppCNName "投资私人管家"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "cx-ssg"
#define MyAppExeName "invest-concierge.exe"
#define MyAppDataDir "{localappdata}\invest-concierge"

[Setup]
AppId={{8E6A2F4C-1B3D-4E5A-9C7B-2D8F0A1E3B45}
AppName={#MyAppName}
AppVerName={#MyAppCNName} {#MyAppVersion}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL=https://github.com/cx-ssg/invest-concierge
AppSupportURL=https://github.com/cx-ssg/invest-concierge/issues
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppCNName}
; per-user 安装：{autopf} 自动解析为 %LOCALAPPDATA%\Programs\invest-concierge，
; 无 UAC 弹窗，与数据目录同在 LOCALAPPDATA 语义一致（Inno 官方 UsedUserAreasWarning 建议）
PrivilegesRequired=lowest
OutputDir=..\..\dist_m4
OutputBaseFilename=invest-concierge-setup-v{#MyAppVersion}
SetupIconFile=..\..\assets\icon.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
; 中文界面（Inno 自带语言包）
ShowLanguageDialog=no

[Languages]
; Inno 6 官方发行版不带简中 isl（第三方包才有）；英文界面 + [Messages] 覆盖关键文案，
; 避免引入需随仓库分发的外部语言文件
Name: "english"; MessagesFile: "compiler:Default.isl"

[Messages]
; 关键文案中文化（覆盖 Default.isl）；未覆盖的仍为英文，可接受
SetupAppTitle=投资私人管家 安装向导
SetupWindowTitle=投资私人管家 安装向导
SelectDirDesc=选择安装位置
SelectDirLabel3=安装程序将把 [name] 安装到下列文件夹。点击"浏览"可更改。
ReadyLabel2b=准备安装
FinishedHeadingLabel=投资私人管家 安装完成
FinishedLabelNoIcons=投资私人管家已安装到你的电脑。%n%n点击"完成"退出安装向导。
FinishedLabel=投资私人管家已安装到你的电脑。%n%n点击"完成"退出安装向导。
UninstallAppTitle=卸载 投资私人管家
UninstallAppFullTitle=卸载 投资私人管家
ConfirmUninstall=确定要完全移除投资私人管家及其所有组件吗？

[Files]
; 主程序（onefile，59MB）
Source: "..\..\dist_m4\invest-concierge.exe"; DestDir: "{app}"; Flags: ignoreversion
; README（GitHub 网络不佳时本地可看）
Source: "..\..\README.md"; DestDir: "{app}"; Flags: ignoreversion
; LICENSE
Source: "..\..\LICENSE"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
; 开始菜单组：主程序（起始位置=数据目录，SQLite 落 %LOCALAPPDATA%）
Name: "{group}\{#MyAppCNName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{#MyAppDataDir}"
Name: "{group}\{#MyAppCNName}（浏览器模式）"; Filename: "{app}\{#MyAppExeName}"; Parameters: "--browser"; WorkingDir: "{#MyAppDataDir}"
Name: "{group}\卸载 {#MyAppCNName}"; Filename: "{uninstallexe}"
; 桌面图标（Tasks 默认勾选）
Name: "{autodesktop}\{#MyAppCNName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{#MyAppDataDir}"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "创建桌面图标(&D)"; GroupDescription: "附加任务："

[Dirs]
; 预建数据目录并授当前用户完全控制（防 Program Files 式权限问题）
Name: "{#MyAppDataDir}"; Permissions: users-modify

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "启动 {#MyAppCNName}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; 只清安装目录残留（日志等）；用户数据目录 {#MyAppDataDir} 不在此列——
; 卸载时由 [Code] 询问用户决定是否删除，默认保留

[Code]
// 卸载时询问：是否同时删除用户数据（持仓/日记/会话记录）
// 默认保留——数据无价，误删不可逆（2026-09-02 曾有覆盖持仓事故，宁可保守）
function InitializeUninstall(): Boolean;
var
  DataDir: string;
  Answer: Integer;
  Msg: string;
begin
  Result := True;
  DataDir := ExpandConstant('{#MyAppDataDir}');
  if DirExists(DataDir) then
  begin
    Msg := 'Delete your personal data (holdings, diary, sessions)?' + #13#10 + #13#10 +
      'Located at: ' + DataDir + #13#10 + #13#10 +
      'YES = delete all;  NO = keep data (reinstall will reuse it)';
    Answer := SuppressibleMsgBox(Msg, mbConfirmation, MB_YESNO, IDNO);
    if Answer = IDYES then
    begin
      DelTree(DataDir, True, True, True);
    end;
  end;
end;
