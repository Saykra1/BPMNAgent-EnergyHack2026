# Программный интерфейс DIAGRAM

Код, который генерирует модель, выполняется в песочнице. Перед выполнением в ней уже заданы
`DIAGRAM`, `ROOT_PROCESS_ID`, `ROOT_START_TASK_ID` и `ROOT_END_TASK_ID`. Интерфейс совместим с
примером из задания и дополнен методами для событий, дорожек внешних организаций, сообщений и
аннотаций.

```python
# DIAGRAM API (Python). Переменные ROOT_PROCESS_ID, ROOT_START_TASK_ID, ROOT_END_TASK_ID уже заданы.
# <parent> = ROOT_PROCESS_ID | id дорожки (из add_pool) | id пула | id группы | id подпроцесса.
# Все методы возвращают строковые id.

pool_id, lane_ids = DIAGRAM.add_pool(ROOT_PROCESS_ID, ['Роль 1', 'Роль 2'], 'Организация')  # пул с дорожками
pool_id, _ = DIAGRAM.add_pool(ROOT_PROCESS_ID, [], 'Банк')       # повторный вызов = ещё один пул (другая организация)
pool_id = DIAGRAM.add_black_box_pool('Внешняя система')          # пул-«чёрный ящик» (только сообщения)

t = DIAGRAM.add_task('Имя', parent)              # обычная задача
t = DIAGRAM.add_user_task('Имя', parent)         # выполняет человек в системе
t = DIAGRAM.add_service_task('Имя', parent)      # автоматически выполняет система/сервис
t = DIAGRAM.add_script_task('Имя', parent)       # скрипт
t = DIAGRAM.add_manual_task('Имя', parent)       # ручная работа вне системы
t = DIAGRAM.add_send_task('Имя', parent)         # отправка сообщения/уведомления
t = DIAGRAM.add_receive_task('Имя', parent)      # ожидание сообщения
t = DIAGRAM.add_business_rule_task('Имя', parent)
sp = DIAGRAM.create_subprocess('Имя', parent)    # свёрнутый подпроцесс; его шаги создаются с parent=sp
                                                 # (старт/конец внутри подпроцесса добавляются автоматически)
g = DIAGRAM.add_exclusive_gateway('Вопрос?', parent)   # ИЛИ-ИЛИ (одна ветвь по условию)
g = DIAGRAM.add_parallel_gateway('', parent)           # И (все ветви одновременно) и их слияние
g = DIAGRAM.add_inclusive_gateway('Вопрос?', parent)   # одна или несколько ветвей
g = DIAGRAM.add_event_based_gateway('', parent)        # ветвление по наступившему событию
e = DIAGRAM.add_start_event('Имя', parent, kind=None)  # kind: None | 'message' | 'timer' | 'signal'
e = DIAGRAM.add_end_event('Имя', parent, kind=None)    # kind: None | 'message' | 'error' | 'terminate'
e = DIAGRAM.add_intermediate_event('Имя', parent, 'timer')        # ожидание: 'timer' | 'message'
e = DIAGRAM.add_intermediate_event('Имя', parent, 'message', True) # бросающее событие-сообщение
grp = DIAGRAM.add_group('Этап', parent)          # визуальная группа; элементы создаются с parent=grp
DIAGRAM.add_annotation('Комментарий', element)   # текстовая аннотация к элементу

DIAGRAM.add_link(source, target)                 # поток управления (стрелка)
DIAGRAM.add_link(gateway, target, 'Да')          # подпись условия ветви шлюза
DIAGRAM.add_message_link(source, target, 'Счёт') # поток сообщений между РАЗНЫМИ пулами
```

Правила кода:
- Только присваивания и вызовы DIAGRAM.<метод>(...). Нельзя: import, циклы, if, функции, f-строки, print.
- Аргументы — строковые литералы, None, числа, переменные и индексы вида lanes[0].
- Каждая задача/шлюз должны иметь входящую и исходящую связь; процесс начинается в ROOT_START_TASK_ID
  и заканчивается в ROOT_END_TASK_ID (или в add_end_event для альтернативных исходов).
- Каждая ветвь исключающего/инклюзивного шлюза подписывается (третий аргумент add_link).
- Ветви параллельного шлюза сливаются параллельным шлюзом; ветви исключающего — исключающим
  (или ведут к своим конечным событиям).
- Цикл доработки (возврат на предыдущий шаг) — это связь назад на существующий узел.
- Связи между разными пулами — только add_message_link; внутри пула — только add_link.
- Создавайте элемент в дорожке того участника, который выполняет шаг.

## Отличия от примера в задании

| Метод из задания | Реализация |
|---|---|
| `create_subprocess(name, parent)` | свёрнутый подпроцесс; внутри создаётся отдельная диаграмма с проваливанием в bpmn.io. Старт и конец внутри добавляются автоматически |
| `add_task / add_user_task / add_script_task` | как в задании, плюс `service`, `manual`, `send`, `receive`, `business_rule` |
| `add_pool(parent, lanes)` | возвращает `(pool_id, lane_ids)`; третий аргумент — имя пула. Повторный вызов создаёт второй пул (вторую организацию) со своим процессом |
| `add_link(a, b)` | третий аргумент — подпись условия. Связь между разными пулами автоматически превращается в поток сообщений |
| шлюзы и группы | как в задании, плюс `add_event_based_gateway` |
| новое | `add_start_event`, `add_end_event`, `add_intermediate_event`, `add_message_link`, `add_black_box_pool`, `add_annotation` |

Сообщения об ошибках (неизвестный родитель, связь с контейнером вместо узла, неверный тип события
и т. п.) написаны для модели: в них сказано, что исправить. Они возвращаются ей в repair-loop
вместе с номером строки.
