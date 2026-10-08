; Desinstalación de G-Mini Agent (electron-builder la incluye con nsis.include).
;
; El Python que se prepara en el primer arranque (%LOCALAPPDATA%\G-Mini Agent,
; unos 600 MB) se vuelve a crear solo, así que se borra siempre, salvo cuando
; el desinstalador corre dentro de una actualización. Las conversaciones,
; memorias y ajustes (%APPDATA%\G-Mini Agent) se conservan salvo que el usuario
; pida borrarlos; en modo silencioso (/S) se conservan.

!macro customUnInstall
  ${ifNot} ${isUpdated}
    RMDir /r "$LOCALAPPDATA\G-Mini Agent"
    MessageBox MB_YESNO|MB_ICONQUESTION|MB_DEFBUTTON2 "¿Borrar también tus conversaciones, memorias y ajustes de G-Mini Agent?$\r$\n$\r$\nSi eliges No, se quedan en $APPDATA\G-Mini Agent y vuelven a estar disponibles si lo reinstalas." /SD IDNO IDNO +2
    RMDir /r "$APPDATA\G-Mini Agent"
  ${endIf}
!macroend
