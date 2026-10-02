"""Local GUI opt-in only; CLI and MCP never read this preference to grant access.
Not an OS security boundary against a party with full terminal permissions.
"""
KEY=r'Software\OpenBridge\LocalConsent'
VALUE='ComputerUseOnGuiLaunchV1'
GAME_TRUST_VALUE='GameTrustAutoApproveV1'  # local GUI checkbox: auto-approve windows matching game_trust.json

def load():
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,KEY) as key:
            value,kind=winreg.QueryValueEx(key,VALUE)
        return kind==winreg.REG_DWORD and type(value) is int and value==1
    except (ImportError,OSError):return False

def save(enabled):
    if type(enabled) is not bool:raise ValueError('enabled must be bool')
    import winreg
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER,KEY) as key:
        winreg.SetValueEx(key,VALUE,0,winreg.REG_DWORD,int(enabled))

def load_game_trust():
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,KEY) as key:
            value,kind=winreg.QueryValueEx(key,GAME_TRUST_VALUE)
        return kind==winreg.REG_DWORD and type(value) is int and value==1
    except (ImportError,OSError):return False

def save_game_trust(enabled):
    if type(enabled) is not bool:raise ValueError('enabled must be bool')
    import winreg
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER,KEY) as key:
        winreg.SetValueEx(key,GAME_TRUST_VALUE,0,winreg.REG_DWORD,int(enabled))
