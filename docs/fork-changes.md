# Diferencias de este fork frente a upstream

Este fork ([`factorlibre/nanobot`](https://github.com/factorlibre/nanobot))
añade funcionalidades específicas que no están en
[`HKUDS/nanobot`](https://github.com/HKUDS/nanobot) upstream. Este documento
describe cada cambio funcional en alto nivel y apunta al código donde vive.

El objetivo es que un colaborador nuevo pueda entender rápidamente qué hace
distinto este fork y dónde mirar. Se actualiza cada vez que divergemos más
del upstream.

## Índice

1. [Subagentes especialistas](#1-subagentes-especialistas)
2. [Conocimiento privado por empleado](#2-conocimiento-privado-por-empleado)
3. [Aislamiento de skills entre especialistas](#3-aislamiento-de-skills-entre-especialistas)
4. [Tool `delegate`](#4-tool-delegate)
5. [`SPECIALISTS.md` como directrices compartidas](#5-specialistsmd-como-directrices-compartidas)
6. [`lark-oapi` (Feishu) como dependencia opcional](#6-lark-oapi-feishu-como-dependencia-opcional)
7. [Hardening y robustez](#7-hardening-y-robustez)

---

## 1. Subagentes especialistas

Agentes con identidad propia que el agente principal puede invocar de forma
**síncrona** cuando una consulta encaja con un dominio.

- **Dónde viven**: `workspace/specialists/<nombre>/SOUL.md`.
- **Cómo se invocan**: tool `delegate(specialist="nombre", task="...")` desde
  el agente principal. A diferencia de `spawn` (fire-and-forget), `delegate`
  bloquea la iteración, el especialista procesa, y su resultado vuelve como
  `tool_result` al principal.

Flujo:

```
Usuario ──msg──▶ Orquestador
                     │
                tool: delegate(specialist="orion", task="...")
                     │
              SpecialistRunner.run()
                ├── SPECIALISTS.md (directrices base compartidas)
                ├── SOUL.md del especialista (identidad)
                ├── Contexto privado del empleado (opcional)
                ├── Memoria compartida (MEMORY.md, read-only)
                ├── Historial de sesión (últimos 30 msgs, read-only)
                ├── Skills con tools_module (tools nativas por skill)
                └── Loop LLM (≤ max_iterations)
                     │
              Resultado → tool_result → Orquestador responde al usuario
```

Frontmatter típico de un especialista (`SOUL.md`):

```yaml
---
name: orion
description: "ORION — Sales Intelligence: pedidos, clientes, stock"
triggers: "buscar cliente, ver pedidos, stock, crear pedido"
model: null          # hereda del principal, o override explícito
max_iterations: 25
---
```

- `triggers` — frases de ejemplo que ayudan al principal a decidir si delegar.
  Aparecen en el resumen XML inyectado en el system prompt del principal.
- `model: null` — hereda del modelo del principal. Cualquier otro valor
  (`openai/gpt-5.2`, etc.) fuerza un modelo distinto para ese especialista.
- El parser de frontmatter usa `yaml.safe_load` (tipos nativos: int, bool, list).

**Código relacionado**:
- `nanobot/agent/specialist.py` — `SpecialistLoader` + `SpecialistRunner`
- `nanobot/agent/tools/delegate.py` — `DelegateTool`
- `nanobot/agent/context.py:65` — inyección del `# Specialists` XML summary en
  el system prompt del principal
- `nanobot/templates/AGENTS.md` — guía para que el principal use `delegate`

---

## 2. Conocimiento privado por empleado

Un mismo gateway de nanobot puede atender a varios empleados (del mismo
workspace) con contexto privado distinto sin que se filtre información entre
ellos.

**Mecanismo**: `workspace/users/_map.yaml` mapea `{canal}:{sender_id}` a una
carpeta dentro de `workspace/users/`. Todos los `.md` de la carpeta se
inyectan en el system prompt bajo `## Private context: <carpeta>`.

```yaml
# workspace/users/_map.yaml
"telegram:123456|juan": contable
"slack:U045ABC": contable        # mismo empleado, canal distinto
"telegram:789012|maria": almacen
```

```
workspace/users/
├── _map.yaml
├── contable/
│   ├── USER.md
│   └── NOTES.md
└── almacen/
    └── USER.md
```

- **Propagación**: el `sender_id` viaja por `InboundMessage` → `AgentLoop` →
  `_LoopHook` → `_set_tool_context` → `DelegateTool` → `SpecialistRunner`,
  así el perfil privado se aplica también al especialista cuando hay
  delegación.
- **Metadata**: el bloque `[Runtime Context]` añade `Sender: <id>` para que
  el modelo sepa con quién habla.
- **Rendimiento**: el mapping se cachea con invalidación por `mtime` para no
  re-parsear el YAML en cada mensaje.
- **Cross-channel**: el mismo empleado puede tener varios `sender_id`
  apuntando a la misma carpeta.

**Aislamiento práctico de sesiones** (por defecto, sin configuración
adicional):

- En **DMs** cada empleado tiene `session_key` propio
  (`telegram:{user_id}`, `slack:{dm_channel}`) — historial, workspace sandbox
  y lock independientes. No hay filtraciones.
- En **grupos/canales compartidos** todos los miembros comparten
  `session_key`. Esto no está cubierto por el fork todavía; un override
  manual en el adapter (`session_key_override = f"{channel}:{chat_id}:{sender_id}"`
  cuando `is_group`) lo resolvería.

**Código relacionado**:
- `nanobot/agent/user_profiles.py` — `UserProfileLoader` (resolve, load,
  caché mtime)
- `nanobot/agent/context.py` — inyección del perfil privado en
  `build_system_prompt` y línea `Sender:` en `_build_runtime_context`
- `nanobot/agent/tools/delegate.py` — propagación de `sender_id` al
  `SpecialistRunner`
- `nanobot/agent/specialist.py` — carga del perfil privado en el prompt
  del especialista

---

## 3. Aislamiento de skills entre especialistas

Cada skill puede declarar `tools_module` en su frontmatter, vinculando tools
nativas a la skill en lugar de al agente. El `SkillsLoader` aplica reglas de
visibilidad distintas según quién consuma las skills:

| Origen | Agente principal | Especialista X | Especialista Y |
|---|---|---|---|
| `workspace/skills/` (sin `shared: false`) | Sí | Sí | Sí |
| `workspace/skills/` (con `shared: false`) | Sí | No | No |
| `workspace/specialists/X/skills/` | No | Sí | No |
| `workspace/specialists/Y/skills/` | No | No | Sí |
| Builtins | Sí | Sí | Sí |

- **`shared_only`**: flag del `SkillsLoader` usado por los especialistas.
  Filtra del workspace las skills con `shared: false`. No afecta a skills
  propias del especialista ni a builtins.
- **`extra_skills_dirs`**: lista de directorios adicionales (p.ej. la carpeta
  `skills/` del propio especialista) que tienen precedencia sobre las de
  workspace.
- **`_is_shared`**: lee el frontmatter del skill directamente del disco para
  evitar recursión con `load_skill` al chequear el flag.

Orden de resolución en `load_skill` (igual que en `list_skills`):

```
extra_skills_dirs > workspace/skills > builtins
```

Frontmatter de una skill con tools (`SKILL.md`):

```yaml
---
name: gextia-commercial
description: Consulta y gestión de clientes y pedidos
tools_module: nanobot_gextia.commercial_tools
always: true
---
```

El `tools_module` apunta a un módulo Python con `get_tools() -> list[Tool]`.
`SpecialistRunner` lo importa dinámicamente y registra las tools devueltas en
el registry de ese especialista. El nombre del módulo se valida contra un
regex de identificador dotted antes de llegar a `importlib.import_module`
— nombres con path traversal o caracteres raros se rechazan y se loguean.

**Código relacionado**:
- `nanobot/agent/skills.py` — `SkillsLoader.shared_only`,
  `extra_skills_dirs`, `_is_shared`, orden unificado en `load_skill` y
  `list_skills`
- `nanobot/agent/specialist.py` — `_build_skills_loader`,
  `_load_custom_tools` (incluye la validación del `tools_module`)

---

## 4. Tool `delegate`

Expone la delegación a specialists como una tool síncrona estándar del
agente principal.

- **Schema**: dos parámetros obligatorios, `specialist` (nombre) y `task`
  (string libre).
- **Contexto**: recibe `channel`, `chat_id`, `sender_id` vía
  `set_context()`, calcula el `session_key` correspondiente y lo pasa a
  `SpecialistRunner.run()` para que el especialista acceda al historial y
  al perfil privado del empleado.
- **Resultado**: el texto devuelto por el especialista se prefija con
  `[Specialist: NAME]` para que el principal pueda atribuir correctamente
  la respuesta al usuario.
- **Errores**: se capturan dentro de `execute()` y vuelven como `tool_result`
  con formato `Error delegating to specialist 'X': ...`; el fallo del
  especialista nunca tumba el loop del principal.

**Supresión de progreso redundante**: cuando las únicas tool calls de un
turno son `delegate`, `_LoopHook.before_execute_tools` no emite ni el
pensamiento preliminar ("Voy a preguntar al especialista…") ni el
`tool_hint`. Turnos mixtos (p. ej. `delegate` + `read_file`) siguen
mostrando progreso como antes.

**Comparativa rápida**:

| | `delegate` (specialist) | `spawn` (subagent) |
|---|---|---|
| Ejecución | Síncrona, bloquea la iteración | Asíncrona, fire-and-forget |
| Identidad | Por `SOUL.md` propio | Genérico |
| Memoria | Compartida con workspace | Compartida con workspace |
| Historial | Últimos 30 msgs read-only | No accede |
| Retorno | `tool_result` al principal | Mensaje independiente al bus |
| Uso típico | Consultas de dominio | Tareas de fondo, batch |

**Código relacionado**:
- `nanobot/agent/tools/delegate.py`
- `nanobot/agent/loop.py` — `_set_tool_context`, `_LoopHook.before_execute_tools`

---

## 5. `SPECIALISTS.md` como directrices compartidas

Fichero opcional en `workspace/SPECIALISTS.md` con directrices que se cargan
para **todos** los especialistas antes de su `SOUL.md` propio — presentación
al usuario, confirmación antes de escrituras, no compartir datos entre
empleados, formato de respuesta, etc.

El principal **no** lo ve: es un contrato compartido entre los especialistas.

Se incluye una plantilla en `nanobot/templates/SPECIALISTS.md` que se copia
al workspace en el primer arranque vía `sync_workspace_templates` junto con
el resto de bootstrap files (AGENTS.md, SOUL.md, USER.md, TOOLS.md).

**Código relacionado**:
- `nanobot/templates/SPECIALISTS.md` — plantilla
- `nanobot/agent/specialist.py:_build_specialist_prompt` — carga del fichero
- `nanobot/utils/helpers.py:sync_workspace_templates` — copia al workspace

---

## 6. `lark-oapi` (Feishu) como dependencia opcional

`lark-oapi` pesa ~241 MB instalado y sólo hace falta si activas el canal
Feishu. Se ha movido al extra `[feishu]` del `pyproject.toml` para que los
despliegues que no usan Feishu no paguen ese coste.

Para habilitar Feishu:

```bash
uv pip install "nanobot-ai[feishu]"
```

Los imports de `nanobot/channels/feishu.py` son tolerantes a que `lark_oapi`
no esté instalado — el canal simplemente no se registra.

---

## 7. Hardening y robustez

Cambios defensivos que no son features visibles pero importan para producción:

- **Parsing de frontmatter**: `SpecialistLoader._parse_frontmatter` y
  `SkillsLoader._read_workspace_skill_metadata` usan `yaml.safe_load` en
  lugar de un split manual por `:` (que se rompía con valores con dos
  puntos como URLs).
- **Validación de `tools_module`**: regex de identificador dotted antes de
  `importlib.import_module`. Nombres malformados, con path traversal o con
  caracteres no ASCII se rechazan y se loguean — nunca llegan al importer.
- **Errores en especialistas**: `SpecialistRunner.run()` y
  `_load_custom_tools()` usan `logger.exception` (traza completa en logs)
  y devuelven al usuario un mensaje corto que no filtra detalles internos
  de la excepción (`"Error executing specialist 'X'. Check logs for details."`).
- **Caché de `UserProfileLoader`**: el mapping YAML se cachea con
  invalidación por `mtime` — no se re-parsea en cada mensaje.
- **Consistencia `load_skill` ↔ `list_skills`**: mismo orden de resolución
  (extra > workspace > builtins) y misma política de `shared_only`.
  Antes había una asimetría que permitía a un especialista cargar una skill
  no compartida.
