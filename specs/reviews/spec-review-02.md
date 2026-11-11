# Spec Review 02 — SPEC-020 / SPEC-030 / SPEC-040

**Fecha:** 2026-11-08 · **Revisor:** Spec Reviewer (read-only) · **Fase:** SDD paso 2
**Alcance:** `specs/020-domain-core.md`, `specs/030-sqlite-adapter.md`, `specs/040-application-services.md`
**Base de comparación:** `specs/README.md`, `_template.md`, `DECISIONS.md` (D-001…D-007), `INDEX.md`,
`000-system-architecture.md`, `010-data-model.md` y los requisitos de dominio de SPEC-020.

```
Verdict: FAIL
Specs revisados: SPEC-020 (020-domain-core.md), SPEC-030 (030-sqlite-adapter.md), SPEC-040 (040-application-services.md)
Gaps: 28 (bloqueantes: 9, menores: 19)
```

---

## Lo que sí pasa (verificado, no asumido)

| Check | Resultado |
|---|---|
| Frontmatter completo (11 campos) | ✔ las 3 specs: `id, title, component, status, version, spec_revision, depends_on, implements, acceptance, owner, created, updated` |
| 10 secciones del template, en orden | ✔ las 3: 1 Responsabilidad · 2 Contrato · 3 Datos · 4 Reglas · 5 Flujo · 6 Errores · 7 AC · 8 Regeneration · 9 Provenance · 10 Out of scope |
| Entidades Pydantic vs DDL de 010 | ✔ 13 tablas con modelo: **0 campos faltantes, 0 campos de más** (nombres idénticos columna a columna; `issue` = 31/31, `team` = 20/20, `project` = 16/16, `member` = 11/11, `label` = 10/10, `comment` = 9/9, `workflow_state` = 8/8, `attachment` = 8/8, `cycle` = 7/7, `issue_history` = 7/7, `project_milestone` = 6/6, `issue_relation` = 5/5, `workspace` = 4/4). Ver gap 16 por las tablas sin modelo. |
| Enums 020 vs 010 | ✔ `Priority` 0-4 con `No priority/Urgent/High/Medium/Low`; `WorkflowStateType` 6; `RelationType` 4 (`blocks/duplicate/related/similar`); `LabelGroupType` 2 (`multiSelect/singleSelect`); `ProjectStatusType` 6; `EstimationType` 5 (`notUsed/exponential/fibonacci/linear/tShirt`); `TeamVisibility` 3; `MilestoneStatus` 4. Textualmente iguales al `CHECK` de 010 y a lo que declara SPEC-020. |
| Cobertura Gherkin (REQ-F con ≥1 escenario) | ✔ 020 17/17 REQ-F (23 escenarios) · 030 22/22 (22) · 040 24/24 (24). Sin `Scenario Outline`, sin DocStrings, sin bullets dentro de pasos. |
| Framing moralizante | ✔ ninguna ocurrencia en las 3 specs (ni "ético", ni "fair use", ni advertencias normativas) |
| "etc." / prosa vaga en §2 | ✔ no aparece; firmas en bloque `python` |

---

## Gaps

### Bloqueantes

**1. [SPEC-030 §2.2 y §4.4] BLOQUEANTE — el reintento de transacción es imposible con `@contextmanager`.**
El contrato declara `@contextmanager def write(self) -> Iterator[Repos]` (030:147-152) y el algoritmo
de §4.4 (030:757-777) reintenta con `for attempt in 1..3: BEGIN IMMEDIATE / yield repos / COMMIT`
dentro del generador. Un generator-based context manager **no puede** re-ejecutar el cuerpo del
`with`: al segundo `yield`, `contextlib` levanta `RuntimeError("generator didn't stop")`, así que
REQ-F035 ("se reintenta la transacción completa … rehaciendo la transacción") y el AC de 030:906-914
no son implementables tal cual. Además 040:962 prohíbe reintentar en el caso de uso ("el único
reintento es el de SQLITE_BUSY, y vive en SPEC-030"), con lo que no queda capa donde hacerlo.
**Cambiar:** elegir una de dos y reflejarlo en §2.2/§4.4/REQ-F035 — (a) `write()` deja de ser
`contextmanager` y pasa a `write(fn: Callable[[Repos], T]) -> T` (o `begin()`/`commit()` explícitos con
el retry dentro del adaptador), o (b) el reintento se acota a `BEGIN IMMEDIATE` (solo el arranque de
la transacción, sin re-ejecutar el cuerpo) y REQ-F035/§4.4 se reescriben en consecuencia.

**2. [SPEC-030 §2.2 ↔ SPEC-040 §5] BLOQUEANTE — `uow.clock` y `uow.idgen` no existen en el contrato de la UnitOfWork.**
030:139-160 declara `__init__(..., *, clock: Clock, idgen: IdGen, ...)` y expone **solo**
`connection`, `write()`, `read()`, `close()`; su §2 dice "todo símbolo no listado es privado". Pero
040 usa `uow.clock.now()` (040:567), `uow.idgen.new()` (040:568, 615) y hasta `uow.history.record`
(040:906, en el checklist de tests). 040 no es implementable contra el contrato declarado de 030.
**Cambiar:** agregar a 030 §2.2 `@property def clock(self) -> Clock` y `@property def idgen(self) ->
IdGen` (o pasar `clock`/`idgen` como parámetros de los casos de uso, y entonces corregir 040 §5 y
§2.2).

**3. [SPEC-030 §2.4 + §4 R6] BLOQUEANTE — no hay canal para que los repos de `read()` rechacen escrituras.**
R6 y REQ-F033 (030:667-668, 697) exigen que "los repos obtenidos de `read()` lancen
`Conflict(code='READ_ONLY_UOW')` en cualquier método de escritura", pero el constructor es único y
normativo —`__init__(self, conn, *, clock, idgen)` (030:236-239)—, `Repos` es un `frozen` con siete
campos (030:128-136) sin flag, y `read()` construye "los siete repos … con esa misma conexión"
(030:174-175). No hay parámetro ni atributo por donde el repo sepa que está en modo lectura.
**Cambiar:** declarar `__init__(self, conn, *, clock, idgen, readonly: bool = False)` y que
`read()` construya los repos con `readonly=True` (una línea en §2.4, otra en §2.2 y el AC de 030:892-897
queda verificable).

**4. [SPEC-020 §2.2] BLOQUEANTE — `IssueView.is_visible` sombrea la propiedad heredada y da dos valores distintos.**
`Issue` define `is_visible` como **property** (020:352-354) y `IssueView(Issue)` la redefine como
**campo** (020:357-361, "agrega exactamente `priority_label`, `is_open`, `is_visible`"). Reproducido
con pydantic 2.13.4: la clase emite `UserWarning: Field name "is_visible" in "IssueView" shadows an
attribute in parent "Issue"`, y para `IssueView(..., is_visible=False, archived_at=None)` el atributo
`iv.is_visible` devuelve `True` (la property) mientras `iv.model_dump()["is_visible"]` devuelve
`False` (la columna de `v_issue`). El AC de 020:846-851 afirma que la entidad espeja la view: con dos
fuentes de verdad no se puede verificar.
**Cambiar:** en `Issue`, reemplazar la property `is_visible` por un `@computed_field` con otro nombre
(p. ej. `is_visible_effective`) **o** no redeclarar el campo en `IssueView` y leer la columna con
`object.__getattribute__`/alias (p. ej. `row_is_visible` mapeado en el adaptador). Elegir una y
declararla explícitamente en §2.2 y en la invariante de mapeo de 030 §4 R8.

**5. [SPEC-030 §2.4 `positions` ↔ SPEC-040 §4 R8 y §5.2 paso 7] BLOQUEANTE — no hay forma de leer las posiciones del estado destino.**
R8 (040:476-478) exige que `before`/`after` tomen "los vecinos del estado destino" y que `first`/`last`
usen la primera/última posición **de ese estado**. El único método declarado es
`positions(team_id, scope: Literal["sort","board","sub"], parent_id=None)` (030:273), sin parámetro de
estado: devuelve el board del team entero. El flujo lo confirma al no filtrar: `posiciones = [p for
(iid, p) in tx.issues.positions(issue.team_id, "board")]` y luego `posiciones[0]` / `posiciones[-1]`
(040:608-613). Con `board_order` por estado (que es como lo ordena 030 SQL-6, 030:436-446) el cálculo
cruza columnas y produce posiciones incorrectas.
**Cambiar:** agregar `state_id: str | None = None` a `IssueRepo.positions` (030:273) con "filtra por
`state_id` cuando `scope='board'`" y usarlo en 040:608; o declarar explícitamente que `board_order` es
global por team y corregir 030 SQL-6 y 040 R8 en consecuencia (decisión de producto, va a
`DECISIONS.md`).

**6. [SPEC-010 R2 ↔ SPEC-030 §2.5 / §4 R4 / §6] BLOQUEANTE — la estrategia de `next_number` no es la misma en las dos specs (ítem 6 del review).**
010:329-330 dice: "`issue.number` = `MAX(number)+1` del team … `UNIQUE(team_id, number)` es la red de
seguridad, **con hasta 3 reintentos**". 030:332-334 dice que "el reintento de §4 R4 cubre el caso de
una escritura externa", pero R4 (030:661-664) reintenta **solo `SQLITE_BUSY`**, y §4.4 atrapa
únicamente `sqlite3.OperationalError` (030:766). Una colisión de `UNIQUE(team_id, number)` es
`IntegrityError` y en §6 se traduce a `Conflict(rule='DUPLICATE_NUMBER')` (030:833) **sin reintento**:
el reintento que 010 promete no existe en ninguna capa, porque 040 tampoco puede reintentar (R1 +
040:962).
**Cambiar:** o 030 implementa el reintento de número (capturar `UNIQUE(team_id, number)` dentro de
`write()`, recalcular `MAX+1` y reinsertar hasta 3 veces, antes de mapear a `Conflict`), o 010 R2
pierde la frase "con hasta 3 reintentos" y dice que la colisión se propaga como `Conflict`. Como
cambia una spec aprobada (010), la resolución va a `DECISIONS.md` con bump de `spec_revision`.

**7. [SPEC-030 §2.4 `set_state` ↔ SPEC-040 §4 R7] BLOQUEANTE — regla de negocio duplicada en el adaptador.**
030 §1 dice "no aplica reglas de negocio" (030:33-35), pero `IssueRepo.set_state` "setea `state_id`,
**hitos** (`started_at`/`completed_at`/`canceled_at`) según `state.type`, `updated_at`" (030:271). 040
R7 (040:471-475) define esos hitos con precisión (solo si era NULL, limpiar el opuesto, conservar
`started_at` al reabrir) y 040 §5.2 paso 8 los calcula **antes** de llamar `set_state` (040:615-617).
Dos implementaciones de la misma regla, con criterios distintos, y la de 030 no tiene la información
para cumplir R7 (no conoce el estado anterior).
**Cambiar:** `set_state(issue_id, state, *, now)` se limita a `state_id` + `updated_at` (+ los hitos
que 040 le pasa explícitamente si se prefiere una sola sentencia), y R7 queda como dueño único en 040.
Actualizar 030 §2.4, §3 (columna "Columnas escritas") y REQ-F037/AC en consecuencia.

**8. [SPEC-040 §2 y §2.3] BLOQUEANTE — faltan casos de uso que los bordes ya consumen (agujero de alcance).**
El puerto de aplicación de 050 §2.3 declara 44 funciones; la tabla de reconciliación de 040 §2.3
(040:376-394) mapea 11 y no menciona **15 símbolos sin contraparte** en 040 §2 (040:51-60):
`create_label`, `list_labels`, `archive_label`, `create_member`, `list_members`, `get_member`,
`set_member_active`, `add_attachment`, `list_comments`, `list_teams`, `get_team`, `list_projects`,
`get_project`, `list_cycles`, `get_cycle`. No es drift de firma: **no existe el caso de uso**. Y como
A1 prohíbe `cli/ → adapters/` y 040 R1 prohíbe SQL en `app/`, no hay camino para `hifa label add`,
`hifa member`, `hifa team list`, `hifa project list` ni para adjuntar links (040:427-428 dice
explícitamente que nada escribe `attachment`, pese a que 030 §3 declara su INSERT y 050 tiene
`add_attachment`). Tampoco hay `create_label`, aunque D-007 agregó `hifa label add` a la CLI.
**Cambiar:** agregar a 040 §2 los comandos y casos de uso de lectura/escritura faltantes (`Member`,
`Label`, `Attachment` y los `list_*/get_*` de team/project/cycle/label/comment), o —si se decide que
esos read models los consumen los bordes directamente desde 030— declararlo explícitamente y registrar
la excepción a A1 en `DECISIONS.md`. Nota: `AddLabel.create_if_missing` (040:160-166) ya implica un
INSERT en `label` que §3.1 no declara (040:411 lista solo `issue_label`).

**9. [SPEC-020 §1/§2 + SPEC-030 §2.2 + SPEC-040 §1] BLOQUEANTE — `hifa/ports.py` no tiene spec dueña y `Clock`/`IdGen` no tienen firma.**
000 §3 (000:57) declara la capa "Puertos · `hifa/ports.py` · Protocols de repos, UoW, Clock, IdGen",
020 prohíbe importarla (020:44-45) y delega el tiempo en "el puerto `Clock`" (§4 R10), 030 tipa su
constructor con `clock: Clock, idgen: IdGen` (030:141-144) y 040 afirma que `idgen.new()` y
`clock.now()` entran por constructor (040:38-39). Ninguna spec de `specs/` declara `ports.py` en
`implements:` (INDEX:10-21) ni define las firmas (`now() -> datetime` aware UTC, `new() -> str` ULID).
Un implementador no puede escribir tres componentes coherentes sin inventarlas.
**Cambiar:** asignar `src/hifa/ports.py` a una spec (la natural es 030, que ya lo consume, o una nueva
SPEC-011) y declarar los Protocols exactos, incluidos los de repos y UoW que hoy se mencionan sin
firma.

### Menores

**10. [SPEC-020/030/040 frontmatter · línea 6] MENOR — `status` contradice `INDEX.md`.**
Las tres specs dicen `status: draft`; `INDEX.md:13-15` dice `review` para las tres, y el encargo las
ubica en Spec Review (SDD paso 2). **Cambiar:** `status: review` en las tres (o corregir `INDEX.md`,
pero `draft` significa "el contrato puede cambiar", que no es lo que se está evaluando).

**11. [SPEC-020 §4.2 y SPEC-030 §4.2 · IDs] MENOR — 26 IDs de requisito colisionan con SPEC-001.**
No hay duplicados *entre* 020/030/040, pero sí contra la vertical: 001 define `REQ-F001…REQ-F037` y
**020 define REQ-F010…REQ-F026** (020:684-700) y **030 define REQ-F030…REQ-F037** (030:694-701);
además `REQ-N011` está en 001 (= migraciones forward-only) y en 020 (= cobertura ≥95 %, 020:704).
Ejemplo de colisión real de contenido: `REQ-F010` en 001 es "los hitos temporales se derivan del
`type`" y en 020 es "los 8 enums exponen los valores del `CHECK`". Son 26 IDs distintos con el mismo
nombre. D-004 (DECISIONS:46-57) asigna `F001…F037` a 001 y P-02 reconoce la deuda, pero la deuda
incluye una colisión, no solo un formato. **Cambiar:** renumerar 020/030/040 al formato canónico
`REQ-F<NNN>-<NN>` (D-004) —o al menos fuera del rango 001— en el próximo bump de estas specs, y marcar
P-02 como bloqueante de publicación.

**12. [SPEC-020 §2 · tabla de símbolos] MENOR — constantes públicas no declaradas.**
§2 dice "todo símbolo no listado acá es privado (`_prefijo`)" (020:49-51), pero `INITIAL_GAP` y
`MIN_GAP` (020:448-449) y `MAX_TITLE`/`MAX_BODY`/`MAX_DESCRIPTION` (020:490-492) se declaran sin
underscore y **no** figuran en la tabla de §2 (020:53-60). Un borde que quiera mostrar `MAX_TITLE`
tendría que importar un símbolo "privado". **Cambiar:** agregarlos a la tabla de §2 como constantes
estables (los tests de contrato las necesitan) o prefijarlos con `_`.

**13. [SPEC-020 §2.6] MENOR — los constructores de las excepciones no tienen firma.**
`HifaError.__init__(message=None, *, code=None, details=None)` (020:573-575) y las subclases declaran
sus kwargs **en comentarios**: `# kwargs: rule: str, field: str | None, value: Any | None`
(020:587-602). Pero todo `rules.py` y los AC llaman `Validation(rule='TITLE_EMPTY', field='title',
value='')` (020:857, 925) y `NotFound(resource=..., key=...)`. Con "firmas exactas, sin `...`" eso es
una firma faltante en el contrato. **Cambiar:** escribir la firma real de cada subclase, p. ej.
`def __init__(self, message: str | None = None, *, rule: str, field: str | None = None,
value: Any | None = None, details: Mapping[str, Any] | None = None) -> None: ...`.

**14. [SPEC-020 §6 · línea 768] MENOR/MAYOR — `InvalidTransition` nunca se lanza y se atribuye a R5.**
La tabla de §6 dice que `InvalidTransition` (exit 5 / 409) se usa en "transición prohibida por **R5** u
otra regla de estado", pero R5 se implementa con `validate_close`, que lanza
`Validation(CLOSE_WITH_OPEN_CHILDREN)` (020:536-537, 653-655, REQ-F018), y `validate_transition`
documenta "cualquier transición entre tipos está permitida; no hay máquina de estados rígida"
(020:540-543). En las tres specs no hay una sola instrucción `raise InvalidTransition(...)`. Resultado:
el exit 5 que D-001 conserva en el mapa global es inalcanzable y el AC de 040 (REQ-F066 →
`Validation`) contradice la tabla. **Cambiar:** quitar "u otra regla de estado" y declarar
`InvalidTransition` reservada (o eliminarla y ajustar D-001), o definir qué transición la produce
(p. ej. reabrir una issue `canceled` desde un borde restringido) con su REQ.

**15. [SPEC-020 §2.5 `validate_transition`] MENOR — promete una validación que no tiene código de error.**
El docstring dice "Valida team (`validate_state_team`), **estado archivado** y cierre con hijos
abiertos" (020:540-543), pero no hay `rule` para "estado archivado" ni fila en §6 de 020 (020:758-769)
ni en §6 de 040 (040:635-663), y `WorkflowState.archived_at` existe en el modelo (020:242). **Cambiar:**
agregar `Validation(rule='STATE_ARCHIVED', field='state_id')` a la firma, a la tabla de §6 de 020 y a
la de §6 de 040, o borrar la mención.

**16. [SPEC-020 §3 y §2.2] MENOR — tablas del DDL sin modelo ni dueño.**
La tabla de §3 (020:614-629) cubre 12 tablas y la afirmación de 020:408 ("no existe ningún campo …
que no exista como columna") se cumple, pero quedan fuera `team_membership` (010:127-133), que no
tiene modelo, ni repo en 030, ni caso de uso en 040 (tabla huérfana que quedará vacía para siempre), y
`team_previous_key` (010:331-333, migración 003), para la que 020 no ofrece modelo ni `Team.previous_keys`
—siendo que 010 R3 dice que "el team registra el cambio en `team.previous_keys`"—, y `schema_migration`
(010:267-272), razonable que quede fuera pero sin declararlo. **Cambiar:** agregar `TeamMembership` (o
declarar la tabla deprecada en 010), agregar `TeamPreviousKey`/`previous_keys` o eliminar de 010 R3 la
frase que la promete, y decir explícitamente en §10 que `schema_migration` es infraestructura del
migrador y no se modela en dominio.

**17. [SPEC-020 §5 · línea 730] MENOR — referencia cruzada rota.**
"el number que el repo ya reservó (**SPEC-030 §4.2**)" — §4.2 de 030 son los REQ-F (030:690-715); la
reserva del número está en **§2.5 SQL-1** (030:322-334) y el flujo en §5.2 (030:799-809). **Cambiar:**
citar `SPEC-030 §2.5 (SQL-1)`.

**18. [SPEC-030 §8 · línea 1066] MENOR — conteo de tablas desactualizado.**
"`sqlite3 /tmp/hifa_test.db ".tables"    # 15 tablas + issue_fts`". Con D-006 ya son 16 tablas en
`001`+`002` (`schema_migration`) y 17 con la migración 003 (`team_previous_key`); el mismo error está
en el AC de 010:353 ("existen las 15 tablas"). **Cambiar:** "17 tablas + `issue_fts`" y corregir 010 §5
en el mismo cambio (bump de `spec_revision` de 010).

**19. [SPEC-030 §2.4] MENOR — sentinel indefinido y firmas incompletas en cinco repos.**
`LabelRepo.list(workspace_id, *, team_id: str | None = _UNSET, ...)` (030:289-290) usa `_UNSET`, que
**no se define en ninguna spec** (el `UNSET` de 040 vive en `app/commands.py`, no en `adapters/`), y
mezcla el sentinel con `str | None`. Además `ProjectRepo.get_milestone(milestone_id)` no declara
retorno, y `LabelRepo`/`ProjectRepo`/`CycleRepo`/`CommentRepo`/`HistoryRepo` se describen en prosa con
parámetros sin tipo (030:288-312), lo que rompe "firmas exactas" de §2. **Cambiar:** definir
`UNSET` en `connection.py`/`uow.py` como parte del contrato, tipar `team_id: str | None | Unset`, y
pasar los cinco repos a tablas `Método | Firma | Notas` como `TeamRepo`/`IssueRepo`.

**20. [SPEC-040 §5.1 · línea 546] MENOR — el flujo usa `ws` sin obtenerlo.**
Paso 3: `tx.teams.get_by_key(ws.id, cmd.team)`, pero ningún paso anterior lee el workspace, y 030
ofrece `workspace()` y `ensure_workspace()` (030:252-253) que el flujo no menciona. §3.1 sí dice que
`create_issue` lee `workspace`. **Cambiar:** insertar el paso `ws = tx.teams.ensure_workspace(...)` (o
`workspace()` + `NotFound`) antes del paso 3.

**21. [SPEC-040 §5.1 · líneas 574-575] MENOR — `or` confunde 0.0 con "ausente".**
`sort_order=cmd.sort_order or ranking.last(...)` y `board_order=cmd.board_order or ranking.last(...)`:
`0.0` es una posición válida (`between` puede devolver 0.0, 020:456-457) y quedaría reemplazada por
`last()`. **Cambiar:** `cmd.sort_order if cmd.sort_order is not None else ranking.last(...)`.

**22. [SPEC-040 §3.2 I10 vs §4.1 R5] MENOR — dos reglas distintas para el estado por defecto.**
I10 (040:446) dice "`triage` si `triage_enabled`, si no `is_default`, **si no `Backlog`**"; R5
(040:466-468) dice "si no hay ninguno, el **primer estado `unstarted` por `position`**; si el team no
tiene estados, `Validation(TEAM_WITHOUT_STATES)`". Con un team sin `is_default` y sin `Backlog`, las
dos reglas dan resultados distintos. **Cambiar:** dejar una sola (recomendado R5, que es la
verificable) y alinear I10 con ella.

**23. [SPEC-040 REQ-N033 · líneas 534-535] MENOR — el presupuesto de sentencias contradice §3.1.**
REQ-N033 promete "≤ 6 sentencias … a lo sumo 3 lecturas de referencia" para `update_issue`, pero §3.1
(040:407) declara leer `issue`, `team`, `workflow_state`, `member`, `project`, `cycle`, `issue`
(parent) **y** `ancestor_chain`: entre 6 y 8 lecturas antes del UPDATE. **Cambiar:** subir el
presupuesto (p. ej. "≤ 10 sentencias, ≤ 8 lecturas") o justificar en §3.1 cuáles lecturas son
condicionales.

**24. [SPEC-040 REQ-F079 · línea 523] MENOR — referencia a un REQ que no existe en 030.**
"(SPEC-030 **REQ-F070**)" — 030 define `REQ-F030…REQ-F051` (030:694-715); `REQ-F070` es de 040
(`unrelate_issues`). El requisito que se quiere citar es **REQ-F050** ("ninguna operación ejecuta
`DELETE FROM issue`", 030:714). **Cambiar:** citar `SPEC-030 REQ-F050`.

**25. [SPEC-040 §7 AC REQ-F073 · línea 797] MENOR — "uno por cada type" es incompatible con los nombres de §9.**
El AC exige "6 `workflow_state`, **uno por cada type**", pero §9 Provenance fija los nombres
"Backlog, Todo, In Progress, In Review, Done, Canceled" (040:940-942) = `backlog, unstarted, started,
started, completed, canceled`: cinco types, dos `started` y ningún `triage`. El escenario no es
verificable como está escrito. **Cambiar:** o el AC dice "6 estados, con al menos uno de cada type
`backlog`/`unstarted`/`started`/`completed`/`canceled`", o el set por defecto incluye un `triage` y
pierde un `started` (y se actualiza §9).

**26. [SPEC-040 §2.2 · línea 349 y nota 367-368] MENOR — `get_issue(db, ref)` no puede abrir `UnitOfWork.read()`.**
La nota dice que `get_issue` usa `UnitOfWork.read()` "para que las 7 queries vean el mismo snapshot",
pero la firma solo recibe `db: Database` y `UnitOfWork.__init__` exige `clock` e `idgen` (030:139-144)
que `Database` no provee (`Database` solo tiene `path`, `connect()`, `ro_connection()`, 030:91-98).
**Cambiar:** `Database.uow(*, clock, idgen, readonly=True) -> UnitOfWork` en 030 §2.1, o
`get_issue(db, ref)` → `get_issue(uow, ref)` con la convención de las lecturas.

**27. [SPEC-020 §7, SPEC-030 §7, SPEC-040 §7] MENOR — 16 escenarios con más de un `When`.**
Regla del review: un `When` por `Scenario`. Incumplen: **020** (5) — "between detecta un rango
inválido y un hueco agotado" (020:816-822), "needs_rebalance y rebalance reparan el grupo" (824-831),
"estimate fuera de la escala…" (866-873), "no se cierra una issue con sub-issues abiertas" (881-887),
"un label de otro team es rechazado" (908-915); **030** (6) — 883-890, 906-914, 923-930, 940-947,
975-983, 1010-1016; **040** (5) — 692-701, 703-710, 727-734, 835-842, 844-852. Además hay `Given`
después de `Then` en 020:820, 020:871 y 020:913 (Gherkin lo parsea, pero rompe el patrón
Given-When-Then). **Cambiar:** partir cada escenario en dos (`@REQ-…` repetido, que ya se usa en 020
para REQ-F011/F014) y mover el segundo `Given` a un escenario propio.

**28. [SPEC-020 §10 · línea 1023 vs SPEC-040] MENOR — `branch_name` no se genera en ninguna capa.**
020 §10 delega "Generación de ULIDs y de `branch_name` (puerto `IdGen` + `app/`)" a la aplicación,
pero 040 no menciona `branch_name` en ningún caso de uso ni en §5.1 (el `Issue(...)` de 040:568-577 no
lo setea) ni en §3.1. La columna existe en el DDL (010:207), en `Issue` (020:344) y en las dos
sentencias de 030 (344, 363): quedará siempre `NULL`. **Cambiar:** definir en 040 la regla de
generación (o `None` explícito) y agregarla a §5.1 paso 13, o moverla a §10 "Out of scope" de 040.

---

## Prioridad sugerida para el Fix Agent

1. **Gaps 1-3 (SPEC-030 §2.2/§2.4/§4.4)** — sin ellos el adaptador no se puede implementar y 040 no
   compila contra su contrato. Son cuatro cambios locales (firma de `write`, dos properties,
   `readonly` en el constructor de repos) y probablemente un `DECISIONS.md` (D-008) por el retry.
2. **Gaps 5, 6, 7** — tocan contrato entre capas y una spec ya `approved` (010): requieren entrada en
   `DECISIONS.md` + bump de `spec_revision` (y `version` en 030 si cambia `positions`).
3. **Gap 8** — decisión de alcance (¿040 define `Member`/`Label`/`Attachment` y los `list_*`?), con
   impacto en 050/060/070: conviene resolverlo antes de aprobar 040.
4. **Gaps 4, 9** — correcciones de contrato puntuales (IssueView, ports.py).
5. **Menores** — se pueden agrupar en un solo commit de corrección por spec; los que cambian
   comportamiento observable son 14, 15, 21, 22, 26 y 28.

## Nota de proceso

No se editó ninguna spec ni se tocó git. Este archivo es el único artefacto escrito
(`specs/reviews/spec-review-02.md`). Los `.feature` por componente siguen sin existir (P-01): el
Gherkin revisado es el inline de §7, y por eso los gaps 27 se miden sobre ese texto.
