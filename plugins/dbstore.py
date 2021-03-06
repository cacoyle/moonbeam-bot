from . import plugin
from mysql.connector import Error
import json
import mysql.connector

class DBStorePlugin(plugin.Plugin):
    def __init__(self, web_client, plugin_config):
        super().__init__(web_client=web_client, plugin_config=plugin_config)
        self.__init_db()
        self.__cache = None


    def __init_db(self):
        try:
            self.__conn = mysql.connector.connect(
                host=self._config.get("DB_HOST"),
                database=self._config.get("DB_NAME"),
                user=self._config.get("DB_USER"),
                password=self._config.get("DB_PASSWORD")
            )
        except Error as e:
            self._log.exception(f"Failed to connect to db: {e}")
            self.__conn = None


    def __store_message(self, data):
        if not self.__cache:
            self.__cache = {
                "users": {},
                "channels": {},
                "teams": {},
            }

        if data.get("subtype") == "message_changed":
            message = data.get("message")
            if message:
                client_msg_id = message.get("client_msg_id")
                text = message.get("text")
                if client_msg_id and text:
                    self.__update_message(
                        client_msg_id=client_msg_id,
                        text=text,
                    )
            return

        timestamp = data["ts"].split(".")[0]
        #if "bot_id" in data.keys():
        #    return
        try:
            slack_user_id = data.get("user")
            if not slack_user_id:
                slack_user_id = data.get("bot_id")
        except KeyError as ke:
            return
        try:
            slack_team_id = data["team"]
        except KeyError as e:
            return
        client_msg_id = data.get('client_msg_id')
        slack_channel_id = data["channel"]
        text = data["text"]
        files = []
        if "files" in data.keys():
            files = data['files']
        if "file" in data.keys():
            files.append(data['file'])

        if not slack_team_id in self.__cache["teams"].keys():
            team_id = self.__insert("tbl_teams", "slack_team_id", slack_team_id, "slack_team_id", slack_team_id)
            self.__cache["teams"][slack_team_id] = team_id
        else:
            team_id = self.__cache["teams"][slack_team_id]
        if not slack_user_id in self.__cache["users"].keys():
            columns = ["slack_user_id", "team_id"]
            values = [slack_user_id, team_id]
            if "username" in data.keys():
                columns.append("username")
                values.append(data["username"])
            user_id = self.__insert("tbl_users", columns, values, "slack_user_id", slack_user_id)
            self.__cache["users"][slack_user_id] = user_id
        else:
            user_id = self.__cache["users"][slack_user_id]
        if not slack_channel_id in self.__cache["channels"].keys():
            channel_id = self.__insert("tbl_channels", ["slack_channel_id", "team_id"], [slack_channel_id, team_id], "slack_channel_id", slack_channel_id)
            self.__cache["channels"][slack_channel_id] = channel_id
        else:
            channel_id = self.__cache["channels"][slack_channel_id]
        team_url = self.__select(f"select team_url from tbl_teams where id = {team_id}")
        if len(team_url) > 0 and len(team_url[0]) > 0 and team_url[0][0]:
            team_url = team_url[0][0]
            archive_ts = data["ts"].replace(".", "")
            archive_url = "/".join([team_url, "archives", slack_channel_id, f"p{archive_ts}"])
        else:
            archive_url = None
        message_id = self.__insert_message(team_id, channel_id, user_id, timestamp, client_msg_id, archive_url, text)
        self.__handle_files(files, message_id)
        if "user_profile" in data.keys():
            self.__handle_user_profile(data['user_profile'], user_id)


    def __select(self, query):
        if not self.__conn or not self.__conn.is_connected():
            self.__init_db()
        cursor = self.__conn.cursor()
        cursor.execute(query)
        records = cursor.fetchall()
        cursor.close()
        return records


    def __get_id(self, table, slack_id_column, slack_id):
        if not self.__conn or not self.__conn.is_connected():
            self.__init_db()
        cursor = self.__conn.cursor()
        query = f"select id from {table} where {slack_id_column} = '{slack_id}'"
        cursor.execute(query)
        records = cursor.fetchall()
        cursor.close()
        if records:
            return records
        return False


    def __insert(self, table, columns, values, slack_id_column, slack_id):
        item_id = self.__get_id(table, slack_id_column, slack_id)
        if item_id:
            return item_id[0][0]
        if not self.__conn or not self.__conn.is_connected():
            self.__init_db()
        cursor = self.__conn.cursor()
        if isinstance(columns, list):
            columns = ", ".join(columns)
        if isinstance(values, list):
            for index, value in enumerate(values):
                if isinstance(value, int):
                    self._log.debug(f"inserting {str(value)} at index {value}")
                    values[index] = str(value)
                else:
                    values[index] = value.replace(u"'", u"\'")
            self._log.debug(values)
            values = u", ".join("'" + value + "'" for value in values)
        else:
            values = f"'{values}'"
        query = f"insert into {table}({columns}) values ({values})"
        self._log.debug(query)
        cursor.execute(query)
        self.__conn.commit()
        item_id = self.__get_id(table, slack_id_column, slack_id)
        cursor.close()
        return item_id[0][0]


    def __get_message_id(self, timestamp, user_id):
        if not self.__conn or not self.__conn.is_connected():
            self.__init_db()
        cursor = self.__conn.cursor()
        query = "select id from tbl_messages where timestamp = %s and user_id = %s"
        cursor.execute(query, (timestamp, user_id))
        records = cursor.fetchall()
        cursor.close()
        if records:
            return records[0][0]
        return False


    def __insert_message(self, team_id, channel_id, user_id, timestamp, client_msg_id, archive_url, text):
        message_id = self.__get_message_id(timestamp, user_id)
        if message_id:
            return message_id
        if not self.__conn or not self.__conn.is_connected():
            self.__init_db()
        cursor = self.__conn.cursor()
        query = "insert into tbl_messages (team_id, channel_id, user_id, timestamp, client_msg_id, archive_url, text) values (%s, %s, %s, %s, %s, %s, %s)"
        self._log.debug("Inserting message:")
        self._log.debug(query % (team_id, channel_id, user_id, timestamp, client_msg_id, archive_url, text))
        cursor.execute(query, (team_id, channel_id, user_id, timestamp, client_msg_id, archive_url, text))
        self.__conn.commit()
        cursor.close()
        message_id = self.__get_message_id(timestamp, user_id)
        self._log.debug(f"Got message ID : ({message_id})")
        return message_id


    def __update_message(self, client_msg_id, text):
        if not self.__conn or not self.__conn.is_connected():
            self.__init_db()
        cursor = self.__conn.cursor()
        query = "update tbl_messages set text = %s where client_msg_id = %s"
        self._log.debug(query % (text, client_msg_id))
        cursor.execute(query, (text, client_msg_id))
        self.__conn.commit()
        cursor.close()


    def __insert_file(self, file_data):
        if not self.__conn or not self.__conn.is_connected():
            self.__init_db()
        cursor = self.__conn.cursor()
        query = "insert into tbl_files (message_id, mimetype, thumb_800, thumb_80, permalink, permalink_public, name) values (%s, %s, %s, %s, %s, %s, %s)"
        values = (
            file_data['message_id'],
            file_data['mimetype'],
            file_data.get('thumb_800'),
            file_data.get('thumb_80'),
            file_data.get('permalink'),
            file_data.get('permalink_public'),
            file_data.get('name'),
        )
        self._log.debug(query % values)
        cursor.execute(query, values)
        self.__conn.commit()
        cursor.close()


    def __handle_files(self, files, message_id):
        for file_data in files:
            file = {
                'message_id': message_id,
                'mimetype': file_data['mimetype'],
            }
            if 'thumb_800' in file_data.keys():
                file['thumb_800'] = file_data['thumb_800']
            if 'thumb_80' in file_data.keys():
                file['thumb_80'] = file_data['thumb_80']
            if 'permalink' in file_data.keys():
                file['permalink'] = file_data['permalink']
            if 'permalink_public' in file_data.keys() and 'is_public' in file_data.keys() and file_data['is_public'] == True:
                file['permalink_public'] = file_data['permalink_public']
            if 'name' in file_data.keys():
                file['name'] = file_data['name']
            elif 'title' in file_data.keys():
                file['name'] = file_data['title']
            self.__insert_file(file)


    def __handle_user_profile(self, user_profile, user_id):
        subs = []
        query = "update tbl_users set"
        if "name" in user_profile.keys():
            query += " username = %s,"
            subs.append(user_profile['name'])
        if "real_name" in user_profile.keys():
            query += " full_name = %s,"
            subs.append(user_profile['real_name'])
        if "image_72" in user_profile.keys():
            query += " avatar_url = %s,"
            subs.append(user_profile['image_72'])
        if not subs:
            return
        if not self.__conn or not self.__conn.is_connected():
            self.__init_db()
        cursor = self.__conn.cursor()
        query = query[:-1]
        query += " where id = %s"
        subs.append(user_id)
        self._log.debug(query % tuple(subs))
        cursor.execute(query, tuple(subs))
        self.__conn.commit()
        cursor.close()


    def receive(self, request):
        try:
            self.__store_message(request)
        except mysql.connector.errors.IntegrityError as ie:
            self._log.warning(f"Refusing to insert duplicate message: {ie}")
        except Exception as e:
            self._log.exception(e)
        return []
