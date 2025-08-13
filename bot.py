import selfcord
import discord
import asyncio
import json
import re
import io
import aiohttp
from typing import Dict, List
import logging
import discord.ext.commands
import os
from datetime import datetime

# 日志配置
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    handlers=[
        logging.FileHandler("bot.log", encoding="utf-8"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# 翻译功能
async def translate_text(text, target_language, api_key, model="gpt-4o-mini"):
    """调用AI接口翻译文本"""
    if not text.strip():
        return text
    
    url = "https://geekai.co/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    
    # 根据目标语言设置提示词
    if target_language.lower() == "chinese":
        prompt = f"请将以下文本翻译成中文，保持原有的格式和语气：\n\n{text}"
    elif target_language.lower() == "english":
        prompt = f"Please translate the following text to English, maintaining the original format and tone:\n\n{text}"
    else:
        return text  # 不支持的语言直接返回原文
    
    data = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": prompt
            }
        ]
    }
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=data) as response:
                if response.status == 200:
                    result = await response.json()
                    translated_text = result.get('choices', [{}])[0].get('message', {}).get('content', '')
                    logger.info(f"翻译成功: {text[:50]}... -> {translated_text[:50]}...")
                    return translated_text
                else:
                    logger.error(f"翻译失败: {response.status} - {await response.text()}")
                    return text
    except Exception as e:
        logger.error(f"翻译异常: {e}")
        return text

def should_translate_message(channel_id):
    """检查是否需要翻译消息"""
    if channel_id in CONFIG["channel_mapping"]:
        channel_config = CONFIG["channel_mapping"][channel_id]
        return channel_config.get("translate", {}).get("enabled", False)
    return False

def get_translate_config(channel_id):
    """获取翻译配置"""
    if channel_id in CONFIG["channel_mapping"]:
        channel_config = CONFIG["channel_mapping"][channel_id]
        return channel_config.get("translate", {})
    return {}

# 加载配置文件
def load_config():
    """从config.json加载配置"""
    try:
        with open('config.json', 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        logger.error("❌ 找不到 config.json 文件")
        return None
    except json.JSONDecodeError as e:
        logger.error(f"❌ config.json 格式错误: {e}")
        return None

# 加载配置
CONFIG = load_config()
if CONFIG is None:
    logger.error("💡 请检查 config.json 文件是否存在且格式正确")
    exit(1)

# 新增：读取关键字过滤、替换、用户过滤配置
KEYWORD_FILTER = CONFIG.get("keyword_filter", {})
KEYWORD_REPLACE = CONFIG.get("keyword_replace", [])
USER_FILTER = CONFIG.get("user_filter", {})

# 新增：过滤和替换函数
def should_forward_message(content, author_id):
    # 用户过滤
    include_users = set(str(uid) for uid in USER_FILTER.get("include", []))
    exclude_users = set(str(uid) for uid in USER_FILTER.get("exclude", []))
    if include_users and str(author_id) not in include_users:
        return False
    if str(author_id) in exclude_users:
        return False
    # 关键字过滤
    include_keywords = KEYWORD_FILTER.get("include", [])
    exclude_keywords = KEYWORD_FILTER.get("exclude", [])
    if include_keywords and not any(k in content for k in include_keywords):
        return False
    if any(k in content for k in exclude_keywords):
        return False
    return True

def replace_keywords(content):
    for rule in KEYWORD_REPLACE:
        content = content.replace(rule.get("from", ""), rule.get("to", ""))
    return content

class MessageForwarder:
    def __init__(self, discord_clients, token_to_user_id=None, user_id_to_client=None):
        self.discord_clients = discord_clients
        self.channel_mapping = CONFIG["channel_mapping"]
        # 创建目标频道到机器人的映射
        self.target_to_bot = {}
        for bot_config in CONFIG["bots"]:
            for target_channel in bot_config["target_channels"]:
                self.target_to_bot[target_channel] = bot_config["token"]
        self.token_to_user_id = token_to_user_id or {}
        self.user_id_to_client = user_id_to_client or {}

    def set_user_id_to_client(self, mapping):
        self.user_id_to_client = mapping

    def set_token_to_user_id(self, mapping):
        self.token_to_user_id = mapping

    async def forward_message(self, source_channel_id: str, message_content: str = "", author_name: str = "未知用户", attachments=None, embeds=None):
        """转发消息到目标频道，始终优先保留原消息内容，embed只有图片时兜底content为'.'，并打印日志"""
        logger.info(f"[转发前] content: {repr(message_content)} | embeds: {len(embeds) if embeds else 0} | attachments: {len(attachments) if attachments else 0}")
        if source_channel_id in self.channel_mapping:
            target_channel_id = self.channel_mapping[source_channel_id]["target"]
            # 找到目标频道对应的机器人token
            target_bot_token = self.target_to_bot.get(target_channel_id)
            if not target_bot_token:
                logger.error(f"❌ 找不到目标频道 {target_channel_id} 对应的机器人")
                return
            # 通过token找到user_id
            target_user_id = self.token_to_user_id.get(target_bot_token)
            if not target_user_id:
                logger.error(f"❌ 找不到机器人token对应的user_id: {target_bot_token}")
                return
            target_client = self.user_id_to_client.get(target_user_id)
            if not target_client:
                logger.error(f"❌ 找不到对应的机器人客户端 user_id: {target_user_id}")
                return
            try:
                target_channel = target_client.get_channel(int(target_channel_id))
                if target_channel:
                    send_kwargs = {}
                    # 优先保留原消息内容
                    if message_content and message_content.strip():
                        send_kwargs['content'] = message_content
                    elif embeds:
                        only_image_embeds = all(
                            (not e.title and not e.description and not e.fields and e.image and e.image.url)
                            for e in embeds
                        )
                        if only_image_embeds:
                            send_kwargs['content'] = '.'
                    if embeds:
                        send_kwargs['embeds'] = embeds
                    logger.info(f"[转发参数] send_kwargs: {send_kwargs}")
                    # 先发文本和embed
                    if send_kwargs:
                        await target_channel.send(**send_kwargs)
                    # 再发附件（如有）
                    if attachments:
                        for attachment in attachments:
                            try:
                                async with aiohttp.ClientSession() as session:
                                    async with session.get(attachment.url) as resp:
                                        if resp.status == 200:
                                            file_data = await resp.read()
                                            file_name = attachment.filename
                                            discord_file = discord.File(io.BytesIO(file_data), filename=file_name)
                                            await target_channel.send(file=discord_file)
                                            logger.info(f"✅ 附件已转发: {file_name}")
                            except Exception as e:
                                logger.error(f"❌ 附件转发失败: {e}")
                    logger.info(f"✅ 消息已转发到频道 {target_channel_id}")
                else:
                    logger.error(f"❌ 找不到目标频道 {target_channel_id}")
            except Exception as e:
                logger.error(f"❌ 转发消息失败: {e}")

async def get_latest_message(channel_id, token):
    """获取频道最新消息"""
    url = f"https://discord.com/api/v10/channels/{channel_id}/messages?limit=1"
    headers = {"Authorization": token}
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers) as response:
                if response.status == 200:
                    messages = await response.json()
                    if messages:
                        return messages[0]
                logger.error(f"获取频道 {channel_id} 最新消息失败: {response.status}")
    except Exception as e:
        logger.error(f"获取频道 {channel_id} 最新消息异常: {e}")
    return None

def process_api_message(message_data):
    """处理 API 返回的消息数据"""
    content = message_data.get('content', '')
    embeds = []
    attachments = []
    
    # 处理 embeds
    for embed_data in message_data.get('embeds', []):
        try:
            embed = discord.Embed.from_dict(embed_data)
            embeds.append(embed)
        except Exception as e:
            logger.error(f"处理 embed 失败: {e}")
    
    # 处理 attachments
    for attachment_data in message_data.get('attachments', []):
        try:
            attachment = discord.File(attachment_data.get('url', ''), filename=attachment_data.get('filename', 'unknown'))
            attachments.append(attachment)
        except Exception as e:
            logger.error(f"处理 attachment 失败: {e}")
    
    return content, embeds, attachments

class MySelfcordClient(selfcord.Client):
    def __init__(self, forwarder):
        super().__init__()
        self.forwarder = forwarder
    
    async def on_ready(self):
        logger.info(f'🎧 监听客户端已登录: {self.user}')
        logger.info('📡 开始监听指定频道...')

    async def on_message(self, message):
        channel_id = str(message.channel.id)
        author_id = str(message.author.id)
        content = message.content
        # embed 转换：to_dict/from_dict
        embeds = []
        attachments = message.attachments if hasattr(message, 'attachments') else []
        embed_conversion_failed = False

        # 先处理主消息上的 embeds（非引用、非快照）
        if hasattr(message, 'embeds') and message.embeds:
            try:
                # 原始字典用于判定是否为“嵌套型”结构
                raw_embed_dicts = []
                converted = []
                for e in message.embeds:
                    raw = e.to_dict()
                    raw_embed_dicts.append(raw)
                    converted.append(discord.Embed.from_dict(raw))
                embeds = converted
                # 嵌套型 embeds 识别：embed 字典出现额外的 message/messages/embeds 字段，
                # 或 description 疑似JSON并包含上述字段
                suspect_nested = False
                for raw in raw_embed_dicts:
                    if any(k in raw for k in ("embeds", "message", "messages")):
                        suspect_nested = True
                        break
                    desc = raw.get("description")
                    if isinstance(desc, str) and (desc.strip().startswith("{") or desc.strip().startswith("[")):
                        try:
                            parsed = json.loads(desc)
                            if isinstance(parsed, dict) and any(k in parsed for k in ("embeds", "message", "messages", "content")):
                                suspect_nested = True
                                break
                        except Exception:
                            pass
                if suspect_nested:
                    embed_conversion_failed = True
                    logger.info("检测到疑似嵌套型 embeds（主消息）")
            except Exception as ex:
                embed_conversion_failed = True
                embeds = []
                logger.error(f"Embed 转换失败(主消息): {ex}")

        # 自动修正：如果 content 为空，优先尝试从引用消息或 message_snapshots 提取内容和 embed
        if not content.strip():
            # 1. Discord.py 的 message.reference
            if hasattr(message, 'reference') and message.reference and hasattr(message.reference, 'resolved') and message.reference.resolved:
                ref = message.reference.resolved
                content = ref.content
                if hasattr(ref, 'embeds'):
                    try:
                        embeds = [discord.Embed.from_dict(e.to_dict()) for e in ref.embeds]
                    except Exception as ex:
                        logger.error(f"Embed 转换失败: {ex}")
                if hasattr(ref, 'attachments'):
                    attachments = ref.attachments
            # 2. 如果有 message_snapshots（自定义结构），也可以解析
            elif hasattr(message, 'message_snapshots') and message.message_snapshots:
                snap = message.message_snapshots[0]
                logger.error(f"message_snapshots[0] dir: {dir(snap)}")
                logger.error(f"message_snapshots[0] dict: {getattr(snap, '__dict__', {})}")
                content = getattr(snap, 'content', '')
                embeds = []
                if hasattr(snap, 'embeds'):
                    raw_embed_dicts = []
                    for e in snap.embeds:
                        try:
                            raw = e.to_dict()
                            raw_embed_dicts.append(raw)
                            embeds.append(discord.Embed.from_dict(raw))
                        except Exception as ex:
                            embed_conversion_failed = True
                            logger.error(f"Embed 转换失败: {ex}")
                    # 嵌套型 embeds 判定（快照）
                    if raw_embed_dicts:
                        suspect_nested = False
                        for raw in raw_embed_dicts:
                            if any(k in raw for k in ("embeds", "message", "messages")):
                                suspect_nested = True
                                break
                            desc = raw.get("description")
                            if isinstance(desc, str) and (desc.strip().startswith("{") or desc.strip().startswith("[")):
                                try:
                                    parsed = json.loads(desc)
                                    if isinstance(parsed, dict) and any(k in parsed for k in ("embeds", "message", "messages", "content")):
                                        suspect_nested = True
                                        break
                                except Exception:
                                    pass
                        if suspect_nested:
                            embed_conversion_failed = True
                            logger.info("检测到疑似嵌套型 embeds（快照）")
                if hasattr(snap, 'attachments'):
                    attachments = snap.attachments
            # 注意：按需触发 HTTP 获取仅在 embeds 异常时进行，已在下方 embed_conversion_failed 分支处理

        # 如果检测到嵌套/异常 embeds（任一转换失败），也走 HTTP 标准化获取
        if embed_conversion_failed:
            logger.info(f"检测到嵌套/异常 embed，尝试通过HTTP获取频道 {channel_id} 最新消息进行标准化")
            latest_message = await get_latest_message(channel_id, CONFIG['listener_token'])
            if latest_message:
                content, embeds, attachments = process_api_message(latest_message)
                logger.info(f"因嵌套embed，已通过HTTP标准化处理频道 {channel_id} 最新消息")

        # 如果 embeds 存在但没有可发送的文本字段（仅图片等），也触发 HTTP 标准化获取
        if not embed_conversion_failed and embeds:
            try:
                only_image_embeds = bool(embeds) and all(
                    (not getattr(e, 'title', None)) and
                    (not getattr(e, 'description', None)) and
                    (not getattr(e, 'fields', [])) and
                    getattr(getattr(e, 'image', None), 'url', None)
                    for e in embeds
                )
            except Exception:
                only_image_embeds = False
            if only_image_embeds:
                logger.info(f"检测到仅图片 embeds，尝试通过HTTP获取频道 {channel_id} 最新消息进行标准化")
                latest_message = await get_latest_message(channel_id, CONFIG['listener_token'])
                if latest_message:
                    api_content, api_embeds, api_attachments = process_api_message(latest_message)
                    # 若HTTP结果提供了文本或更丰富的embed，则采用之，否则保留原始
                    has_textual_embed = any(
                        getattr(e, 'title', None) or getattr(e, 'description', None) or getattr(e, 'fields', [])
                        for e in api_embeds
                    )
                    if (api_content and api_content.strip()) or has_textual_embed:
                        content, embeds, attachments = api_content, api_embeds, api_attachments
                        logger.info(f"因仅图片 embeds，已通过HTTP标准化处理频道 {channel_id} 最新消息")

        if channel_id in CONFIG["channel_mapping"]:
            if not should_forward_message(content, author_id):
                return
            content = replace_keywords(content)
            
            # 检查是否需要翻译
            translate_config = get_translate_config(channel_id)
            if translate_config.get("enabled", False):
                target_language = translate_config.get("target_language", "chinese")
                model = translate_config.get("model", "gpt-4o-mini")
                api_key = CONFIG.get("geekai_api_key", "")
                
                if api_key:
                    # 翻译消息内容
                    if content.strip():
                        logger.info(f"开始翻译消息内容: {content[:50]}... (模型: {model})")
                        translated_content = await translate_text(content, target_language, api_key, model)
                        if translated_content != content:
                            content = translated_content
                            logger.info(f"消息内容已翻译为{target_language}")
                    
                    # 翻译 embeds 中的文本内容
                    if embeds:
                        logger.info(f"开始翻译 {len(embeds)} 个 embeds (模型: {model})")
                        for i, embed in enumerate(embeds):
                            # 翻译 title
                            if hasattr(embed, 'title') and embed.title:
                                try:
                                    translated_title = await translate_text(embed.title, target_language, api_key, model)
                                    if translated_title != embed.title:
                                        embed.title = translated_title
                                        logger.info(f"Embed {i+1} title 已翻译为{target_language}")
                                except Exception as e:
                                    logger.error(f"翻译 Embed {i+1} title 失败: {e}")
                            
                            # 翻译 description
                            if hasattr(embed, 'description') and embed.description:
                                try:
                                    translated_desc = await translate_text(embed.description, target_language, api_key, model)
                                    if translated_desc != embed.description:
                                        embed.description = translated_desc
                                        logger.info(f"Embed {i+1} description 已翻译为{target_language}")
                                except Exception as e:
                                    logger.error(f"翻译 Embed {i+1} description 失败: {e}")
                            
                            # 翻译 fields
                            if hasattr(embed, 'fields') and embed.fields:
                                for j, field in enumerate(embed.fields):
                                    # 翻译 field name
                                    if hasattr(field, 'name') and field.name:
                                        try:
                                            translated_name = await translate_text(field.name, target_language, api_key, model)
                                            if translated_name != field.name:
                                                field.name = translated_name
                                                logger.info(f"Embed {i+1} field {j+1} name 已翻译为{target_language}")
                                        except Exception as e:
                                            logger.error(f"翻译 Embed {i+1} field {j+1} name 失败: {e}")
                                    
                                    # 翻译 field value
                                    if hasattr(field, 'value') and field.value:
                                        try:
                                            translated_value = await translate_text(field.value, target_language, api_key, model)
                                            if translated_value != field.value:
                                                field.value = translated_value
                                                logger.info(f"Embed {i+1} field {j+1} value 已翻译为{target_language}")
                                        except Exception as e:
                                            logger.error(f"翻译 Embed {i+1} field {j+1} value 失败: {e}")
                        
                        logger.info(f"所有 embeds 翻译完成")
            
            logger.info(f"📨 收到来自频道 {channel_id} 的消息: {content[:50]}... (用户: {author_id})")
            if attachments:
                logger.info(f"📎 发现 {len(attachments)} 个附件")
            author_name = message.author.display_name if hasattr(message.author, 'display_name') else str(message.author)
            await self.forwarder.forward_message(channel_id, content, author_name, attachments, embeds)

class MyDiscordClient(discord.Client):
    def __init__(self, intents, token=None):
        super().__init__(intents=intents)
        self.forwarder = None  # 将在主函数中设置
        self._token = token
    
    async def on_ready(self):
        logger.info(f'🤖 转发机器人已登录: {self.user}')
        logger.info('✅ 转发机器人准备就绪!')
        for source_id, mapping in CONFIG["channel_mapping"].items():
            target_channel = self.get_channel(int(mapping["target"]))
            if not target_channel:
                logger.error(f"❌ 目标频道 {mapping['target']} 不可用")

    async def on_message(self, message):
        # 机器人不响应自己的消息
        if message.author == self.user:
            return
        # 只响应 ping 命令
        if message.content == 'ping':
            await message.channel.send('pong from discord.py')

async def start_discord_bot(client, token, user_id_to_client, token_to_user_id):
    try:
        await client.login(token)
        await client.connect()
        user_id = client.user.id
        user_id_to_client[user_id] = client
        token_to_user_id[token] = user_id
    except Exception as e:
        logger.error(f"❌ 机器人登录失败: {e}")

def start_selfcord(selfcord_client):
    async def _start():
        try:
            await selfcord_client.start(CONFIG["listener_token"])
        except Exception as e:
            logger.error(f"❌ 监听账号登录失败: {e}")
    return _start()

async def main():
    intents = discord.Intents.default()
    intents.message_content = True
    intents.guilds = True
    intents.messages = True
    intents.guild_messages = True
    intents.dm_messages = True

    discord_clients = []
    tokens = []
    for bot_config in CONFIG["bots"]:
        client = MyDiscordClient(intents=intents, token=bot_config["token"])
        discord_clients.append(client)
        tokens.append(bot_config["token"])

    forwarder = MessageForwarder(discord_clients)
    for client in discord_clients:
        client.forwarder = forwarder
    selfcord_client = MySelfcordClient(forwarder)

    logger.info("🚀 启动消息转发系统...")
    logger.info(f"📋 监听频道: {list(CONFIG['channel_mapping'].keys())}")
    logger.info(f"🎯 目标频道: {list(set(mapping['target'] for mapping in CONFIG['channel_mapping'].values()))}")
    logger.info(f"🤖 机器人数量: {len(CONFIG['bots'])}")

    user_id_to_client = {}
    token_to_user_id = {}

    for i, client in enumerate(discord_clients):
        try:
            await client.login(tokens[i])
            user_id = client.user.id
            user_id_to_client[user_id] = client
            token_to_user_id[tokens[i]] = user_id
            logger.info(f"✅ {CONFIG['bots'][i]['remark']} 登录成功 (token: {tokens[i][:10]}...)")
        except Exception as e:
            logger.error(f"❌ {CONFIG['bots'][i]['remark']} 登录失败: {e} (token: {tokens[i][:10]}...)")
        await asyncio.sleep(1)

    forwarder.set_user_id_to_client(user_id_to_client)
    forwarder.set_token_to_user_id(token_to_user_id)

    bot_tasks = [asyncio.create_task(client.connect()) for client in discord_clients]
    logger.info("即将启动 selfcord 监听账号...")
    try:
        await selfcord_client.start(CONFIG["listener_token"])
    except Exception as e:
        logger.error(f"❌ 监听账号登录失败: {e}")
    await asyncio.gather(*bot_tasks)

if __name__ == "__main__":
    asyncio.run(main())