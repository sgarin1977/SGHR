from dotenv import load_dotenv
import os
import ssl
from sqlalchemy.ext.asyncio import create_async_engine

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")

ssl_context = ssl.create_default_contextr()
engine = create_async_engine(DATABASE_URL, connect_args={"ssl": ssl_context})

