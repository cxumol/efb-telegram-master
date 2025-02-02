# coding=utf-8
import html
from typing import Tuple, Dict, TYPE_CHECKING, List, Any, Union

from telegram import Message, Update
from telegram.ext import CommandHandler, ConversationHandler, CallbackQueryHandler, MessageHandler, CallbackContext
from telegram.ext.filters import Filters

from ehforwarderbot import coordinator, EFBChannel, EFBMiddleware
from ehforwarderbot.message import EFBMsgCommand
from ehforwarderbot.types import ExtraCommandName
from .constants import Flags
from .locale_mixin import LocaleMixin

if TYPE_CHECKING:
    from . import TelegramChannel


class ETMCommandMsgStorage:
    def __init__(self, command: List[EFBMsgCommand], module: Union[EFBChannel, EFBMiddleware],
                 prefix: str, body: str):
        self.commands = command
        self.module = module
        self.prefix = prefix
        self.body = body


class CommandsManager(LocaleMixin):
    """
    Functions related to Command messages and
    Additional features of slave channels.
    """

    def __init__(self, channel: 'TelegramChannel'):
        self.channel: 'TelegramChannel' = channel
        self.bot = channel.bot_manager
        self.msg_storage: Dict[Tuple[int, int], ETMCommandMsgStorage] = dict()

        self.bot.dispatcher.add_handler(
            CommandHandler("extra", self.extra_listing))
        self.bot.dispatcher.add_handler(
            MessageHandler(
                Filters.regex(r"^/h_(?P<id>[0-9]+)_(?P<command>[a-z0-9_-]+)"),
                self.extra_usage))
        self.bot.dispatcher.add_handler(
            MessageHandler(
                Filters.regex(r"^/(?P<id>[0-9]+)_(?P<command>[a-z0-9_-]+)"),
                self.extra_call))

        self.command_conv = ConversationHandler(
            entry_points=[],
            states={Flags.COMMAND_PENDING: [CallbackQueryHandler(self.command_exec)]},
            fallbacks=[CallbackQueryHandler(self.bot.session_expired)],
            per_message=True,
            per_chat=True,
            per_user=False
        )

        self.bot.dispatcher.add_handler(self.command_conv)

        self.modules_list: List[Any[EFBChannel, EFBMiddleware]] = []
        for i in sorted(coordinator.slaves.keys()):
            self.modules_list.append(coordinator.slaves[i])
        self.modules_list.extend(coordinator.middlewares)

    def register_command(self, message: Message, commands: ETMCommandMsgStorage):
        message_identifier = (message.chat.id, message.message_id)
        self.command_conv.conversations[message_identifier] = Flags.COMMAND_PENDING
        self.msg_storage[message_identifier] = commands

    def command_exec(self, update: Update, context: CallbackContext) -> int:
        """
        Run a command from a command message.
        Triggered by callback message with status `Flags.COMMAND_PENDING`.

        This method is a part of the command message conversation handler.

        Returns:
            The next state
        """

        chat_id = update.effective_chat.id
        message_id = update.effective_message.message_id
        callback = update.callback_query.data

        index = (chat_id, message_id)

        if not callback.isdecimal():
            msg = self._("Invalid parameter: {0}. (CE01)").format(callback)
            self.msg_storage.pop(index, None)
            self.bot.edit_message_text(text=msg, chat_id=chat_id, message_id=message_id)
            return ConversationHandler.END
        elif not (0 <= int(callback) < len(self.msg_storage[index].commands)):
            msg = self._("Index out of bound: {0}. (CE02)").format(callback)
            self.msg_storage.pop(index, None)
            self.bot.edit_message_text(text=msg, chat_id=chat_id, message_id=message_id)
            return ConversationHandler.END

        callback = int(callback)
        command_storage = self.msg_storage[index]
        module = command_storage.module
        command = command_storage.commands[callback]
        prefix = command_storage.prefix

        # Clear inline buttons.
        update.callback_query.edit_message_reply_markup(None)

        fn = getattr(module, command.callable_name, None)
        if fn is not None:
            msg = fn(*command.args, **command.kwargs)
        else:
            module_id = str(module)
            if isinstance(module, EFBChannel):
                module_id = module.channel_id
            elif isinstance(module, EFBMiddleware):
                module_id = module.middleware_id
            msg = self._command_fallback(*command.args,  # type: ignore
                                         __channel_id=module_id,
                                         __callable=command.callable_name,
                                         **command.kwargs)
        self.msg_storage.pop(index, None)
        # self.bot.edit_message_text(prefix=prefix, text=msg,
        #                            chat_id=chat_id, message_id=message_id)
        if msg is None:
            return ConversationHandler.END
        self.bot.answer_callback_query(
            prefix=prefix, text=msg,
            chat_id=chat_id, message_id=message_id,
            callback_query_id=update.callback_query.id
        )
        return ConversationHandler.END

    def extra_listing(self, update: Update, context: CallbackContext):
        """
        Show list of additional features and their usage.
        Triggered by `/extra`.
        """
        msg = self._("<i>Click the link next to the name for usage.</i>\n")
        for idx, i in enumerate(self.modules_list):
            if isinstance(i, EFBChannel):
                msg += "\n\n<b>{0} {1}".format(
                    html.escape(i.channel_emoji),
                    html.escape(i.channel_name))
                if i.instance_id:
                    msg += " ({})".format(html.escape(i.instance_id))
                msg += "</b>"

            elif isinstance(i, EFBMiddleware):
                msg += "\n\n<b>{} ({})</b>".format(
                    html.escape(i.middleware_name),
                    html.escape(i.middleware_id)
                )
            else:
                # This should not occur as modules_list shall
                # consist of only EFBChannel and EFBMiddleware instances
                continue
            extra_fns = i.get_extra_functions()
            if extra_fns:
                for fn in extra_fns:
                    fn_name = f"/h_{idx}_{fn}"
                    # noinspection PyUnresolvedReferences
                    msg += "\n- <b>{}</b> {}".format(
                        html.escape(extra_fns[fn].name),
                        html.escape(fn_name)
                    )
            else:
                msg += "\n" + self._("No command found.")
        self.bot.send_message(update.effective_chat.id, msg, parse_mode="HTML")

    def extra_usage(self, update: Update, context: CallbackContext):
        groupdict = context.match.groupdict()
        if int(groupdict['id']) >= len(self.modules_list):
            return self.bot.reply_error(update, self._("Invalid module id ID. (XC03)"))

        channel = self.modules_list[int(groupdict['id'])]
        functions = channel.get_extra_functions()

        if groupdict['command'] not in functions:
            return self.bot.reply_error(update, self._("Command not found in selected module. (XC04)"))

        command = getattr(channel, groupdict['command'])

        msg = "<b>{0} {1}".format(
            html.escape(channel.channel_emoji),
            html.escape(channel.channel_name))
        if channel.instance_id:
            msg += " ({})".format(html.escape(channel.instance_id))
        msg += "</b>"

        fn_name = "/%s_%s" % (groupdict['id'], groupdict['command'])
        msg += "\n\n{} <b>({})</b>\n{}".format(
            html.escape(fn_name),
            html.escape(command.name),
            html.escape(command.desc.format(function_name=fn_name)))
        self.bot.send_message(update.effective_chat.id, msg, parse_mode="HTML")

    def extra_call(self, update: Update, context: CallbackContext):
        """
        Invoke an additional feature from slave channel.
        """
        groupdict = context.match.groupdict()
        if int(groupdict['id']) >= len(coordinator.slaves):
            return self.bot.reply_error(update, self._("Invalid module ID. (XC01)"))

        slaves = coordinator.slaves

        channel = slaves[sorted(slaves)[int(groupdict['id'])]]
        functions = channel.get_extra_functions()

        command_name = ExtraCommandName(groupdict['command'])

        if groupdict['command'] not in functions:
            return self.bot.reply_error(update, self._("Command not found in selected module. (XC02)"))

        header = "{} {}: {}\n-------\n".format(
                channel.channel_emoji, channel.channel_name,
                functions[groupdict['command']].name  # type: ignore
        )
        msg = self.bot.send_message(update.message.chat.id,
                                    prefix=header, text=self._("Please wait..."))

        result = functions[ExtraCommandName(groupdict['command'])](
            " ".join(update.message.text.split(' ', 1)[1:]))

        self.bot.edit_message_text(prefix=header, text=result,
                                   chat_id=update.message.chat.id, message_id=msg.message_id)

    def _command_fallback(self, *args, __channel_id: str, __callable: str, **kwargs) -> str:
        return self._("Error: Command is not found in the channel.\n"
                      "Function: {channel_id}.{callable}\n"
                      "Arguments: {args!r}\nKeyword Arguments: {kwargs!r}").format(channel_id=__channel_id,
                                                                                   callable=__callable,
                                                                                   args=args,
                                                                                   kwargs=kwargs)
