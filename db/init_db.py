from db.crud import init_db
import logging 

logging.basicConfig(level = logging.INFO)

if __name__ == "__main__":
    print("Initializing Database")
    init_db()
    print("Database created successfully:")