from app.core.database import Base, engine
from app.models.parcel import Parcel
from app.models.ticket import Ticket
from app.models.reroute import Reroute
from app.models.notification import Notification
from app.models.message import Message
from app.models.pending_action import PendingAction
from app.models.intervention import Intervention


def create_tables():
    Base.metadata.create_all(bind=engine)
    print("Tables created successfully in Postgres.")


if __name__ == "__main__":
    create_tables()