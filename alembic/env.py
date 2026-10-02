from alembic import context
from sqlalchemy import engine_from_config,pool
from arm_ripper_vision.config import get_settings
from arm_ripper_vision.models import Base
config=context.config
config.set_main_option("sqlalchemy.url",get_settings().resolved_database_url)
target_metadata=Base.metadata
if context.is_offline_mode():
    context.configure(url=config.get_main_option("sqlalchemy.url"),target_metadata=target_metadata,literal_binds=True)
    with context.begin_transaction(): context.run_migrations()
else:
    e=engine_from_config(config.get_section(config.config_ini_section,{}),prefix="sqlalchemy.",poolclass=pool.NullPool)
    with e.connect() as c:
        context.configure(connection=c,target_metadata=target_metadata)
        with context.begin_transaction(): context.run_migrations()
