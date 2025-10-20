# # myapp/mongo_models.py
# from mongoengine import Document, EmbeddedDocument
# from mongoengine.fields import (
#     StringField, DateTimeField, ListField, EmbeddedDocumentField, DictField
# )
# from datetime import datetime


# class Message(EmbeddedDocument):
#     sender = StringField(required=True)
#     text = StringField(required=True)
#     timestamp = DateTimeField(default=datetime.utcnow)


# class Chat(Document):
#     meta = {"collection": "chats"}  # use the actual collection name from MongoDB

#     user_id = StringField(required=False)      # Link to Django User.id (as str)
#     platform = StringField(required=False)
#     created_at = DateTimeField(default=datetime.utcnow)
#     messages = ListField(EmbeddedDocumentField(Message))
#     metadata = DictField()                     # optional extra data

#     def __str__(self):
#         return f"Chat({self.user_id}, {self.platform}, {self.created_at})"





# myapp/mongo_test.py
from mongoengine import Document, StringField

class TestDoc(Document):
    name = StringField(required=True)
