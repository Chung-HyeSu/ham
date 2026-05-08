Set sh  = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

base    = fso.GetParentFolderName(WScript.ScriptFullName)
hamster = base & "\hamster.py"
tmp     = fso.GetSpecialFolder(2) & "\ham_tmp.txt"

' Python 확인
If sh.Run("cmd /c python --version", 0, True) <> 0 Then
    MsgBox "Python이 설치되어 있지 않습니다." & vbCrLf & _
           "https://www.python.org 에서 설치 후 다시 실행해 주세요.", 16, "햄찌"
    WScript.Quit
End If

' pythonw 경로 찾기
sh.Run "cmd /c where pythonw > """ & tmp & """", 0, True
Set f   = fso.OpenTextFile(tmp, 1)
pythonw = Trim(f.ReadLine())
f.Close
fso.DeleteFile tmp

' 패키지 확인 및 설치
If sh.Run("cmd /c python -c ""import PIL, rembg, cv2, pynput""", 0, True) <> 0 Then
    MsgBox "필요한 패키지를 설치합니다. 잠시 기다려 주세요.", 64, "햄찌"
    sh.Run "python -m pip install pillow rembg opencv-python pynput", 1, True
    MsgBox "설치 완료! 햄찌를 시작합니다.", 64, "햄찌"
End If

' 바탕화면 바로가기 생성 (없을 때만)
desktop = sh.SpecialFolders("Desktop")
lnkPath = desktop & "\햄찌.lnk"
If Not fso.FileExists(lnkPath) Then
    Set lnk          = sh.CreateShortcut(lnkPath)
    lnk.TargetPath       = "wscript.exe"
    lnk.Arguments        = """" & base & "\햄찌.vbs"""
    lnk.WorkingDirectory = base
    lnk.IconLocation     = base & "\hamster_icon.ico"
    lnk.Save()
End If

' 햄찌 실행 (CMD 창 없음)
sh.Run """" & pythonw & """ """ & hamster & """", 0, False