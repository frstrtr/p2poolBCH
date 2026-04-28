"""
MTProto-native variant of the p2pool Telegram notification bot.

Same UI and same event wire-format as ``telegram_bot``, but uses Telethon
(MTProto direct) instead of python-telegram-bot (HTTPS Bot API).  Pick
this variant when:

  * The bot host needs to traverse an MTProto Telegram-app proxy
    (the proxytg.live / bella-cook style listed inside the official
    Telegram apps), which the Bot API variant cannot use.
  * Egress to ``api.telegram.org:443`` is blocked but MTProto endpoints
    on UDP/TCP 443/8443/etc. are reachable.

The PTB variant in ``telegram_bot`` remains the default and is fine for
networks that allow plain HTTPS to ``api.telegram.org``.

Both variants share ``telegram_bot.subscriptions`` (one subscriber list)
and ``telegram_bot.messages`` (identical alert wording), so switching
between them is a deploy-time choice that preserves user state.
"""
