## Servidores MCP disponibles y sus herramientas

Los siguientes servidores MCP están configurados y listos. Puedes usar las herramientas directamente con `mcp_call_tool(server_id=..., tool=..., arguments={...})` SIN necesidad de listar primero.

{{mcp_tools_summary}}

**Uso directo:** No necesitas ejecutar `mcp_list_servers` ni `mcp_list_tools` antes de usar una herramienta MCP — esas tools NO EXISTEN. Ya conoces las tools disponibles arriba. Simplemente llama `mcp_call_tool` con el server_id, nombre de tool y argumentos correctos. NO inventes nombres de tools que no aparezcan en esta lista.

### Notas criticas por servidor

**mcpcontrol (si esta disponible):**
- Nombres de teclas SIEMPRE en minuscula: `ctrl`, `shift`, `alt`, `enter`, `a`-`z`, `f1`-`f24`. NUNCA usar `Control`, `Shift`, `Alt` con mayuscula inicial — la validacion los rechazara.
- `hold_key` con `state: "down"` REQUIERE el parametro `duration` (milisegundos, min 1, max 10000) aunque el schema no lo marque como "required". Sin `duration`, la llamada FALLARA.
- Para atajos de teclado (Ctrl+S, Ctrl+C, etc.), usa `press_key_combination` con `keys: ["ctrl", "s"]` en vez de secuencias hold_key down/up.
- `minimize_window` y `restore_window` existen pero NO estan soportadas — siempre devuelven error.
- Consulta la seccion "Lista completa de tools MCPControl" en el prompt principal para referencia detallada de las 23 tools.
