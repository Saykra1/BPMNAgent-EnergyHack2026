# DIAGRAM API (Python). Переменные ROOT_PROCESS_ID, ROOT_START_TASK_ID, ROOT_END_TASK_ID уже заданы.
# <parent> = ROOT_PROCESS_ID | id дорожки (из add_pool) | id пула | id группы | id подпроцесса.
# Все методы возвращают строковые id. Необязательный id='t1' задаёт id элемента явно (как в плане).

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
DIAGRAM.set_details(element, 'Точная цитата из описания', 'Допущение или пустая строка', 'Срок или пустая строка', ['Документ'])

DIAGRAM.add_link(source, target)                 # поток управления (стрелка)
DIAGRAM.add_link(gateway, target, 'Да')          # подпись условия ветви шлюза
DIAGRAM.add_link(gateway, target, 'Иначе', default=True)  # ветка по умолчанию исключающего шлюза
DIAGRAM.add_message_link(source, target, 'Счёт') # поток сообщений между РАЗНЫМИ пулами
