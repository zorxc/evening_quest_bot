from datetime import datetime, time

import logging
import json
import asyncio

from aiogram import Bot, Dispatcher, executor, types
from aiogram.contrib.fsm_storage.mongo import MongoStorage
from aiogram.dispatcher import FSMContext
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from task_state import TaskState
from db_repository import DbRepository, Task, Captain, CaptainTask

logging.basicConfig(level=logging.INFO)

file = open('config.json', 'r', encoding='utf-8')

config = json.load(file)

mongo_storage_name = config['mongo_storage_name']
db_connect_string = config['db_connect_string']
token = config['bot_api_token']

file.close()

bot = Bot(token=token)

mongo_storage = MongoStorage(db_name=mongo_storage_name)
dp = Dispatcher(bot, storage=mongo_storage)
db_repo = DbRepository(db_connect_string)


async def send_notification(message, is_end=False):
    captains = db_repo.find_all(Captain, None)

    if is_end:
        for task in db_repo.find_all(Task, Task.is_active):
            task.is_active = False
            db_repo.db.commit()

            if task.is_last:
                winner = db_repo.find_max(Captain, Captain.points)[0]
                message = 'И победителем Вечернего квеста становится команда ' + winner.team_name + \
                          ' и её лидер @' + winner.tg_name + '! Поздравляем!\n' \
                          'Остальных попрошу не расстраиваться, вы также получите небольшой приз. ' \
                          'Хотелось бы поблагодарить вас всех за участие в нашем квесте. ' \
                          'Вы умные ребята и многие из вас станут хорошими специалистами.' \
                          'Желаю вам успехов в учебе и сдаче летней сессии.\n' \
                          'Знание - сила!'
                break

        await mongo_storage.reset_all()

    for captain in captains:
        if captain.tg_id is None:
            continue

        await bot.send_message(chat_id=captain.tg_id,
                               text=message)


async def send_task(message: types.Message, state: FSMContext):
    if time(6, 0, 0) < message.date.time() < time(22, 0, 0):
        await message.answer('Я не понимаю о чем ты. Дождись нужного момента.')
        return

    args = message.get_args()

    if len(args) == 0:
        return

    task_id = args

    task = db_repo.find_first(Task, Task.task_id == task_id)

    if task is None:
        await message.answer('Задание по данному QR-коду не найдено.')
        return

    if not task.is_active:
        await message.answer('Не-не, дружок. Кто не успел - тот опоздал!')
        return

    captains_tasks = db_repo.find_all(CaptainTask, CaptainTask.tg_name == message.from_user.username)

    is_exists = False

    for cp in captains_tasks:
        is_exists = cp.task.number_of_task == task.number_of_task or is_exists

    if is_exists:
        await message.answer('Эй! Ты уже выполнял подобное задание! Не-не, дружок, повторно выполнить не получится.')
        return

    await message.answer('У вас новое сообщение!')
    await message.answer_document(open(task.archive_path + 'message.rar', 'rb'))

    new_captain_task = CaptainTask(tg_name=message.from_user.username,
                                   task_id=task.task_id)

    db_repo.add(new_captain_task)

    await state.set_data({
        'task_id': task.task_id,
        'number_of_task': task.number_of_task,
        'result': task.result,
        'is_last': task.is_last
    })

    await TaskState.task.set()


@dp.message_handler(commands='start', state='*')
async def send_welcome(message: types.Message, state: FSMContext):
    captain = db_repo.find_first(Captain, Captain.tg_name == message.from_user.username)

    if captain is None:
        await message.answer('Такой капитан команды не зарегистрирован.')
        return

    if captain.is_first_start:
        text_welcome = 'Привет! Данный квест создан дабы проверить вашу сообразительность. ' \
                       'Ответ на каждое задание является фразой или набором символов. Ответ не зависит от регистра. ' \
                       'Время, затраченное на выполнение каждого задания, учитывается в общем зачете. ' \
                       'При правильном или неправильном ответе бот будет сообщать вам об этом. ' \
                       'Каждое задание будет отправляться вам архивом. ' \
                       'Максимальное время на каждое задание - 8 часов. ' \
                       'В данном квесте можно пользоваться любыми ресурсами и программами. ' \
                       'Также в квесте не будет подсказок, только ваши личные идеи. ' \
                       'Да прибудет с вами Цикада 3301! ' \
                       'Каждое задание закодировано в QR-коде. Сами QR-коды будут расклеены заранее в 4-ом корпусе. ' \
                       'Удачи, и да победит умнейший!'

        await message.answer(text_welcome)

        captain.is_first_start = False
        captain.tg_id = message.from_user.id
        db_repo.db.commit()

    await send_task(message, state)


@dp.message_handler(state=TaskState.task)
async def process_task(message: types.Message, state: FSMContext):
    data = await state.get_data()

    captain = db_repo.find_first(Captain, Captain.tg_name == message.from_user.username)

    if message.text.upper() != data['result'].upper():
        await message.answer('Это не ответ. Подумай ещё.')
        return

    time_send_message = datetime.strptime(str(message.date.time()), '%H:%M:%S')
    time_start_send_response = datetime.strptime('22:00:00', '%H:%M:%S')
    hours = (time_send_message - time_start_send_response).seconds / 60 ** 2

    if hours <= 3.3:
        captain.points += 2
    elif 3.3 < hours <= 6.6:
        captain.points += 1.5
    elif 6.6 < hours <= 9.9:
        captain.points += 1

    captain_task = db_repo.find_first(CaptainTask, CaptainTask.tg_name == message.from_user.username,
                                      CaptainTask.task_id == data['task_id'])

    captain_task.true_response_date = message.date
    db_repo.db.commit()

    if data['is_last']:
        await message.answer('Вы прошли наш квест! Объявление победителя будет в 6:00 по местному времени.')
    else:
        await message.answer('И это правильный ответ! Ищите следующее задание завтра! Удачи!')

    await state.finish()


async def startup(dp: Dispatcher):
    scheduler = AsyncIOScheduler()

    scheduler.add_job(send_notification, trigger='cron', hour=21, minute=0,
                      kwargs={'message': 'Задания можно будет просканировать уже через час! Приготовьтесь!'})
    scheduler.add_job(send_notification, trigger='cron', hour=22, minute=0,
                      kwargs={'message': 'Сканируйте задания!'})
    scheduler.add_job(send_notification, trigger='cron', hour=2, minute=56,
                      kwargs={'message': 'Задания закроются через 4 минуты! Поторопитесь!'})
    scheduler.add_job(send_notification, trigger='cron', hour=3, minute=0,
                      kwargs={'message': 'Задания закрыты!', 'is_end': True})

    scheduler.start()


if __name__ == '__main__':
    executor.start_polling(dp, on_startup=startup, on_shutdown=lambda _: db_repo.close(), skip_updates=True)
