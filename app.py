from flask import Flask, render_template, request, redirect, session, url_for, flash
import logging
import nltk
from nltk.corpus import wordnet as wn
nltk.download('wordnet')
from config import Config  # Import your configuration (e.g., DATABASE_URL)
from pass_admin import PASSWORDS_ADMIN  # Import admin passwords if needed
from werkzeug.security import check_password_hash, generate_password_hash
import psycopg2
from psycopg2.pool import SimpleConnectionPool
from psycopg2.extras import RealDictCursor
import networkx as nx
from pyvis.network import Network
from transliteration import load_transliteration_map
from transliteration import devanagari_to_gondi
# Add this at the top of your app.py file
json_path = "gondi_proper.json"
gondi_mapping = load_transliteration_map(json_path)

# Configure logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.config.from_object(Config)

# Database configuration (get from config.py)
DATABASE_URL = app.config['DATABASE_URL']
LOGIN_DATABASE_URL = app.config['LOGIN_DATABASE_URL']

if not DATABASE_URL:
    logger.error("DATABASE_URL is not set in config.py")
    raise ValueError("DATABASE_URL is not set in config.py")

if not LOGIN_DATABASE_URL:
    logger.error("LOGIN_DATABASE_URL is not set in config.py")
    raise ValueError("LOGIN_DATABASE_URL is not set in config.py")

# Ensure SSL mode is set to 'require'
if 'sslmode' not in DATABASE_URL:
    DATABASE_URL += '?sslmode=require'

if 'sslmode' not in LOGIN_DATABASE_URL:
    LOGIN_DATABASE_URL += '?sslmode=require'

# Declare global pool variables before usage
pool = None
login_pool = None

# Initialize pool variables only once and handle the connection setup.
def initialize_pools():
    global pool, login_pool
    try:
        # Initialize main database pool
        pool = SimpleConnectionPool(1, 10, DATABASE_URL)
        logger.info("Main database connection pool initialized successfully.")
    except (Exception, psycopg2.Error) as error:
        logger.error("Error while connecting to PostgreSQL (main database): %s", error)
        raise
    try:
        # Initialize login database pool
        login_pool = SimpleConnectionPool(1, 10, LOGIN_DATABASE_URL)
        logger.info("Login database connection pool initialized successfully.")
    except (Exception, psycopg2.Error) as error:
        logger.error("Error while connecting to PostgreSQL (login database): %s", error)
        raise

# Call initialize_pools to set up the pools when the app starts
initialize_pools()

def generate_synset_data(word, pos_code):
    pos_code = pos_code.strip('.').lower() if pos_code else 'n'
    wn_pos_map = {'n': wn.NOUN, 'v': wn.VERB, 'a': wn.ADJ, 'r': wn.ADV}
    wn_pos = wn_pos_map.get(pos_code, wn.NOUN)
    
    lemma = word.replace(' ', '_').lower()
    synsets = wn.synsets(lemma, pos=wn_pos)
    
    if synsets:
        synset = synsets[0]
        return {
            'name': synset.name(),
            'definition': synset.definition(),
            'hypernym': ', '.join([h.name() for h in synset.hypernyms()]),
            'hyponym': ', '.join([h.name() for h in synset.hyponyms()]),
            'pos': f"{synset.pos().lower()}."
        }
    else:
        sanitized_word = lemma
        return {
            'name': f"{sanitized_word}.{pos_code}.01",
            'definition': sanitized_word,
            'hypernym': '',
            'hyponym': '',
            'pos': f"{pos_code}."
        }

def get_db_connection(pool=None):
    if pool is None:
        pool = SimpleConnectionPool(1, 10, DATABASE_URL)  # Fallback to the default pool if not provided
    try:
        conn = pool.getconn()
        logger.debug("Connection obtained from pool.")
        return conn
    except (Exception, psycopg2.Error) as error:
        logger.error("Error while getting connection from pool: %s", error)
        return None

def get_db_connection(login_pool=None):
    if login_pool is None:
        login_pool = SimpleConnectionPool(1, 10, LOGIN_DATABASE_URL)  # Fallback to the default pool if not provided
    try:
        conn = login_pool.getconn()
        logger.debug("Connection obtained from pool.")
        return conn
    except (Exception, psycopg2.Error) as error:
        logger.error("Error while getting connection from pool: %s", error)
        return None




def fetch_words(status_list=['pending', 'reviewed', 'approved']):
    conn = get_db_connection(pool)
    if conn:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        try:
            cur.execute('SELECT * FROM gondi_wordnet WHERE status = ANY(%s) ORDER BY name ASC;', (status_list,))
            words = cur.fetchall()
            return words
        except (Exception, psycopg2.Error) as error:
            logger.error("Error while fetching words from PostgreSQL: %s", error)
            return []
        finally:
            cur.close()
            pool.putconn(conn)
            logger.debug("Connection returned to pool.")
    else:
        return []

@app.route('/', methods=['GET', 'POST'])
def main_page():
    search_query = request.form.get('search', '')
    if search_query:
        approved_entries = fetch_words_by_search(search_query)
    else:
        approved_entries = fetch_words(status_list=['approved'])

    # Get the row count of approved synsets
    approved_count = get_row_count(status_list=['approved'])
    

    return render_template('index.html', approved_entries=approved_entries, search_query=search_query, approved_count=approved_count)


def fetch_words_by_search(search_query, status_list=['approved']):
    conn = get_db_connection(pool)
    if conn:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        try:
            cur.execute("""
                SELECT * FROM gondi_wordnet
                WHERE status = ANY(%s) 
                AND (name ILIKE %s OR words_gondi ILIKE %s OR words_hindi ILIKE %s 
                     OR words_english ILIKE %s OR definition ILIKE %s OR hin_word ILIKE %s)
                ORDER BY name ASC;
            """, (
                status_list,
                f'%{search_query}%', f'%{search_query}%', f'%{search_query}%', 
                f'%{search_query}%', f'%{search_query}%', f'%{search_query}%'
            ))
            words = cur.fetchall()
            logger.debug("Fetched words by search successfully: %s", words)
            return words
        except (Exception, psycopg2.Error) as error:
            logger.error("Error while fetching words from PostgreSQL: %s", error)
            return []
        finally:
            cur.close()
            pool.putconn(conn)
            logger.debug("Connection returned to pool.")
    else:
        logger.error("No database connection available.")
        return []

@app.route('/add_word', methods=['GET', 'POST'])
def add_word():
    if request.method == 'POST':
        # Retrieve form data
        words_english = request.form.get('words_english', '').strip()
        pos = request.form.get('pos', '').strip('.').lower()
        words_hindi = request.form.get('words_hindi', '').strip()
        hin_word = request.form.get('hin_word', '').strip()
        words_gondi = request.form.get('words_gondi', '').strip()
        definition = request.form.get('definition', '').strip()
        hypernym = request.form.get('hypernym', '').strip()
        hyponym = request.form.get('hyponym', '').strip()
        
        # Split English words on semicolons
        english_words = [word.strip() for word in words_english.split(';') if word.strip()]
        if not english_words:
            flash('Please provide at least one English word.', 'danger')
            return redirect(url_for('add_word'))

        conn = get_db_connection(pool)
        if conn:
            cur = conn.cursor()
            try:
                for english_word in english_words:
                    # Generate synset data for each English word
                    wordnet_data = generate_synset_data(english_word, pos)
                    
                    # Prepare values
                    name = wordnet_data['name']
                    pos_to_use = wordnet_data['pos']
                    definition_to_use = definition or wordnet_data['definition']
                    hypernym_to_use = hypernym or wordnet_data['hypernym']
                    hyponym_to_use = hyponym or wordnet_data['hyponym']

                    # Generate words_gondi for each entry (uses form input if provided)
                    words_gondi_current = words_gondi or devanagari_to_gondi(words_hindi, gondi_mapping)

                    cur.execute("""
                        INSERT INTO gondi_wordnet (name, words_gondi, words_hindi, hin_word, words_english, 
                                                   definition, hypernym, hyponym, pos, status)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'pending');
                    """, (
                        name,
                        words_gondi_current,
                        words_hindi,
                        hin_word,
                        english_word,
                        definition_to_use,
                        hypernym_to_use,
                        hyponym_to_use,
                        pos_to_use
                    ))
                
                conn.commit()
                flash('Words added successfully!', 'success')
                return redirect(url_for('add_word'))
            except Exception as error:
                flash('Error adding word.', 'danger')
                logger.error("Error while inserting word into PostgreSQL: %s", error)
            finally:
                cur.close()
                pool.putconn(conn)
        else:
            flash('Database connection error.', 'danger')
            logger.error("No database connection available.")
    return render_template('add_word.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']

        # Check if the username exists in the PASSWORDS_USER dictionary and password matches
        if username in PASSWORDS_ADMIN and PASSWORDS_ADMIN[username] == password:
            session['logged_in'] = True
            return redirect(url_for('admin'))
        else:
            flash('Invalid credentials', 'danger')
            logger.warning("Invalid login attempt.")
            return redirect(url_for('login'))
    
    return render_template('login.html')

@app.route('/login_review', methods=['GET', 'POST'])
def login_review():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        # Use the login_pool to get a connection
        conn = get_db_connection(login_pool)
        if conn:
            cur = conn.cursor()
            try:
                cur.execute("SELECT password, approved FROM login_database WHERE username = %s;", (username,))
                user_data = cur.fetchone()
                
                if user_data:
                    db_password = user_data[0]
                    is_approved = user_data[1]
                    logger.debug(f"Fetched password for user {username}: {db_password}")
                    logger.debug(f"User {username} approved status: {is_approved}")
                    
                    if check_password_hash(db_password, password):
                        if is_approved:
                            # Password matches and user is approved, set session and redirect to review page
                            session['logged_in'] = True
                            return redirect(url_for('review'))
                        else:
                            flash('Your account is pending approval.', 'warning')
                            logger.warning("Pending approval for user: %s", username)
                    else:
                        flash('Invalid credentials', 'danger')
                        logger.warning("Invalid login attempt for user: %s", username)
                else:
                    flash('Invalid credentials', 'danger')
                    logger.warning("User not found: %s", username)
            except (Exception, psycopg2.Error) as error:
                logger.error("Error while checking login credentials: %s", error)
                flash('Error during login', 'danger')
            finally:
                cur.close()
                login_pool.putconn(conn)
                logger.debug("Connection returned to login pool.")
        else:
            flash('Database connection error.', 'danger')
            logger.error("No login database connection available.")
            return redirect(url_for('login_review'))
    return render_template('login_review.html')

@app.route('/create_user', methods=['GET', 'POST'])
def create_user():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        # Hash the password before storing it
        hashed_password = generate_password_hash(password)

        logger.debug(f"Hashed password for user {username}: {hashed_password}")

        # Check if the username already exists
        conn = get_db_connection(login_pool)
        if conn:
            cur = conn.cursor()
            try:
                cur.execute('SELECT * FROM login_database WHERE username = %s;', (username,))
                existing_user = cur.fetchone()
                if existing_user:
                    flash('Username already exists.', 'danger')
                    return redirect(url_for('create_user'))
                else:
                    # Insert the new user with approved status set to False
                    cur.execute('INSERT INTO login_database (username, password, approved) VALUES (%s, %s, FALSE);', (username, hashed_password))
                    conn.commit()
                    flash('User created successfully! Please wait for approval.', 'success')
                    return redirect(url_for('login_review'))  # Redirect to login_review page
            except (Exception, psycopg2.Error) as error:
                flash('Error creating user.', 'danger')
                logger.error("Error while creating user in login database: %s", error)
            finally:
                cur.close()
                login_pool.putconn(conn)
                logger.debug("Connection returned to login pool.")
        else:
            flash('Database connection error.', 'danger')
            logger.error("No login database connection available.")
    return render_template('create_user.html')

@app.route('/admin', methods=['GET', 'POST'])
def admin():
    if not session.get('logged_in'):
        return redirect(url_for('login'))
    
    if request.method == 'POST':
        user_id = request.form.get('id')  # Use 'id' instead of 'user_id'
        action = request.form.get('action')
        
        logger.debug(f"Received id: {user_id}, action: {action}")  # Log received values
        
        if not user_id or not action:
            flash('Invalid request data.', 'danger')
            logger.error("Invalid request data: id or action is missing.")
            return redirect(url_for('admin'))
        
        conn = get_db_connection(login_pool)
        if conn:
            cur = conn.cursor()
            try:
                if action == 'approve':
                    cur.execute('UPDATE login_database SET approved = TRUE WHERE id = %s;', (user_id,))
                    conn.commit()
                    flash('User approved successfully!', 'success')
                    logger.info(f"User {user_id} approved successfully.")  # Log success
                elif action == 'reject':
                    cur.execute('DELETE FROM login_database WHERE id = %s;', (user_id,))
                    conn.commit()
                    flash('User rejected successfully!', 'success')
                    logger.info(f"User {user_id} rejected successfully.")  # Log success
                else:
                    flash('Invalid action.', 'danger')
                    logger.error(f"Invalid action received: {action}")
            except (Exception, psycopg2.Error) as error:
                flash('Error updating user status.', 'danger')
                logger.error(f"Error while updating user status in login database: {error}")  # Log errors
            finally:
                cur.close()
                login_pool.putconn(conn)
                logger.debug("Connection returned to login pool.")
        else:
            flash('Database connection error.', 'danger')
            logger.error("No login database connection available.")
    
    # Fetch all pending users
    conn = get_db_connection(login_pool)
    if conn:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        try:
            cur.execute('SELECT * FROM login_database WHERE approved = FALSE;')
            pending_users = cur.fetchall()
        except (Exception, psycopg2.Error) as error:
            flash('Error fetching pending users.', 'danger')
            logger.error(f"Error while fetching pending users from login database: {error}")
            pending_users = []
        finally:
            cur.close()
            login_pool.putconn(conn)
            logger.debug("Connection returned to login pool.")
    else:
        flash('Database connection error.', 'danger')
        logger.error("No login database connection available.")
        pending_users = []

    return render_template('admin.html', pending_users=pending_users)


@app.route('/logout')
def logout():
    session.pop('logged_in', None)
    logger.info("User logged out.")
    return redirect(url_for('main_page'))

@app.route('/review_entries', methods=['GET'])
def review_entries():
    if not session.get('logged_in'):
        return redirect(url_for('login'))

    search_query = request.args.get('search', '')
    if search_query:
        approved_entries = fetch_words_by_search(search_query, status_list=['approved'])
        pending_entries = fetch_words_by_search(search_query, status_list=['pending'])
        reviewed_entries = fetch_words_by_search(search_query, status_list=['reviewed'])
    else:
        approved_entries = fetch_words(status_list=['approved'])
        pending_entries = fetch_words(status_list=['pending'])
        reviewed_entries = fetch_words(status_list=['reviewed'])
       
    logger.debug("Pending entries: %s", pending_entries)
    logger.debug("Reviewed entries: %s", reviewed_entries)
    logger.debug("Approved entries: %s", approved_entries)
    
    return render_template('review_entries.html', 
                           pending_entries=pending_entries, 
                           reviewed_entries=reviewed_entries, 
                           approved_entries=approved_entries,
                           search_query=search_query)

@app.route('/review', methods=['GET'])
def review():
    if not session.get('logged_in'):
        return redirect(url_for('login_review'))

    search_query = request.args.get('search', '')
    if search_query:
        pending_entries = fetch_words_by_search(search_query, status_list=['pending'])
        reviewed_entries = fetch_words_by_search(search_query, status_list=['reviewed'])
    else:
        pending_entries = fetch_words(status_list=['pending'])
        reviewed_entries = fetch_words(status_list=['reviewed'])
       
    logger.debug("Pending entries: %s", pending_entries)
    logger.debug("Reviewed entries: %s", reviewed_entries)
    return render_template('review.html', pending_entries=pending_entries, reviewed_entries=reviewed_entries, search_query=search_query)

@app.route('/approve_word', methods=['POST'])
def approve_word():
    if not session.get('logged_in'):
        return redirect(url_for('login'))

    word_id = request.form.get('word_id')
    if word_id:
        conn = get_db_connection(pool)
        if conn:
            cur = conn.cursor()
            try:
                cur.execute('UPDATE gondi_wordnet SET status = %s WHERE id = %s;', ('approved', word_id))
                conn.commit()
                flash('Word approved successfully!', 'success')
                logger.info("Word approved successfully: %s", word_id)
            except (Exception, psycopg2.Error) as error:
                flash('Error approving word.', 'danger')
                logger.error("Error while approving word in PostgreSQL: %s", error)
            finally:
                cur.close()
                pool.putconn(conn)
                logger.debug("Connection returned to pool.")
    return redirect(url_for('review_entries'))

@app.route('/pending_word', methods=['POST'])
def pending_word():
    if not session.get('logged_in'):
        return redirect(url_for('login'))

    word_id = request.form.get('word_id')
    if word_id:
        conn = get_db_connection(pool)
        if conn:
            cur = conn.cursor()
            try:
                cur.execute('UPDATE gondi_wordnet SET status = %s WHERE id = %s;', ('pending', word_id))
                conn.commit()
                flash('Word status pending!', 'success')
                logger.info("Word status pending: %s", word_id)
            except (Exception, psycopg2.Error) as error:
                flash('Error approving word.', 'danger')
                logger.error("Error while approving word in PostgreSQL: %s", error)
            finally:
                cur.close()
                pool.putconn(conn)
                logger.debug("Connection returned to pool.")
    return redirect(url_for('review_entries'))

@app.route('/reject_word', methods=['POST'])
def reject_word():
    if not session.get('logged_in'):
        return redirect(url_for('login'))

    word_id = request.form.get('word_id')
    if word_id:
        conn = get_db_connection(pool)
        if conn:
            cur = conn.cursor()
            try:
                cur.execute('DELETE FROM gondi_wordnet WHERE id = %s', (word_id,))
                conn.commit()
                flash('Word rejected successfully!', 'success')
                logger.info("Word rejected successfully: %s", word_id)
            except (Exception, psycopg2.Error) as error:
                flash('Error rejecting word.', 'danger')
                logger.error("Error while rejecting word in PostgreSQL: %s", error)
            finally:
                cur.close()
                pool.putconn(conn)
                logger.debug("Connection returned to pool.")
    return redirect(url_for('review_entries'))

@app.route('/bulk_action', methods=['POST'])
def bulk_action():
    # Get selected IDs and action
    word_ids = request.form.getlist('word_ids')  # Now returns list of strings
    action = request.form.get('action')
    
    if not word_ids or not action:
        flash('No entries selected or invalid action', 'error')
        return redirect(url_for('review_entries'))
    
    valid_actions = ['approve', 'reject', 'pending']
    if action not in valid_actions:
        flash('Invalid action requested', 'error')
        return redirect(url_for('review_entries'))
    
    # Convert IDs to integers
    try:
        word_ids = [int(word_id) for word_id in word_ids]
    except ValueError:
        flash('Invalid entry IDs', 'error')
        return redirect(url_for('review_entries'))
    
    # Get database connection
    conn = get_db_connection(pool)
    if not conn:
        flash('Database connection error', 'error')
        return redirect(url_for('review_entries'))
    
    cur = conn.cursor()
    try:
        if action == 'approve':
            cur.execute(
                "UPDATE gondi_wordnet SET status = 'approved' WHERE id = ANY(%s)",
                (word_ids,)  # Pass as list of integers
            )
        elif action == 'reject':
            cur.execute(
                "DELETE FROM gondi_wordnet WHERE id = ANY(%s)",
                (word_ids,)
            )
        elif action == 'pending':
            cur.execute(
                "UPDATE gondi_wordnet SET status = 'pending' WHERE id = ANY(%s)",
                (word_ids,)
            )
        
        conn.commit()
        flash(f'Successfully {action}ed {len(word_ids)} entries', 'success')
    except Exception as e:
        conn.rollback()
        flash(f'Error: {str(e)}', 'danger')
    finally:
        cur.close()
        pool.putconn(conn)
    
    return redirect(url_for('review_entries'))

@app.route('/bulk_action_review', methods=['POST'])
def bulk_action_review():
    # Get selected IDs and action
    word_ids = request.form.getlist('word_ids')  # Now returns list of strings
    action = request.form.get('action')
    
    if not word_ids or not action:
        flash('No entries selected or invalid action', 'error')
        return redirect(url_for('review_entries'))
    
    valid_actions = ['reject']
    if action not in valid_actions:
        flash('Invalid action requested', 'error')
        return redirect(url_for('review_entries'))
    
    # Convert IDs to integers
    try:
        word_ids = [int(word_id) for word_id in word_ids]
    except ValueError:
        flash('Invalid entry IDs', 'error')
        return redirect(url_for('review_entries'))
    
    # Get database connection
    conn = get_db_connection(pool)
    if not conn:
        flash('Database connection error', 'error')
        return redirect(url_for('review_entries'))
    
    cur = conn.cursor()
    try:
        if action == 'reject':
            cur.execute(
                "DELETE FROM gondi_wordnet WHERE id = ANY(%s)",
                (word_ids,)
            )
        
        conn.commit()
        flash(f'Successfully {action}ed {len(word_ids)} entries', 'success')
    except Exception as e:
        conn.rollback()
        flash(f'Error: {str(e)}', 'danger')
    finally:
        cur.close()
        pool.putconn(conn)
    
    return redirect(url_for('review'))

@app.route('/reject_word_review', methods=['POST'])
def reject_word_review():
    if not session.get('logged_in'):
        return redirect(url_for('login_review'))

    word_id = request.form.get('word_id')
    if word_id:
        conn = get_db_connection(pool)
        if conn:
            cur = conn.cursor()
            try:
                cur.execute('DELETE FROM gondi_wordnet WHERE id = %s', (word_id,))
                conn.commit()
                flash('Word rejected successfully!', 'success')
                logger.info("Word rejected successfully: %s", word_id)
            except (Exception, psycopg2.Error) as error:
                flash('Error rejecting word.', 'danger')
                logger.error("Error while rejecting word in PostgreSQL: %s", error)
            finally:
                cur.close()
                pool.putconn(conn)
                logger.debug("Connection returned to pool.")
    return redirect(url_for('review'))

@app.route('/edit_word/<int:word_id>', methods=['GET'])
def edit_word(word_id):
    if not session.get('logged_in'):
        return redirect(url_for('login'))

    conn = get_db_connection(pool)
    if conn:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        try:
            cur.execute('SELECT * FROM gondi_wordnet WHERE id = %s;', (word_id,))
            word = cur.fetchone()
            if not word:
                logger.error("Word not found with ID: %s", word_id)
                return 'Word not found', 404
            logger.debug("Fetched word successfully: %s", word)
            return render_template('edit_word.html', word=word)
        except (Exception, psycopg2.Error) as error:
            logger.error("Error while fetching word from PostgreSQL: %s", error)
            return 'Error fetching word', 500
        finally:
            cur.close()
            pool.putconn(conn)
            logger.debug("Connection returned to pool.")
    else:
        logger.error("No database connection available.")
        return 'Database connection error', 500
    
@app.route('/edit_word_review/<int:word_id>', methods=['GET'])
def edit_word_review(word_id):
    if not session.get('logged_in'):
        return redirect(url_for('login_review'))

    conn = get_db_connection(pool)
    if conn:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        try:
            cur.execute('SELECT * FROM gondi_wordnet WHERE id = %s;', (word_id,))
            word = cur.fetchone()
            if not word:
                logger.error("Word not found with ID: %s", word_id)
                return 'Word not found', 404
            logger.debug("Fetched word successfully: %s", word)
            return render_template('edit_word_review.html', word=word)
        except (Exception, psycopg2.Error) as error:
            logger.error("Error while fetching word from PostgreSQL: %s", error)
            return 'Error fetching word', 500
        finally:
            cur.close()
            pool.putconn(conn)
            logger.debug("Connection returned to pool.")
    else:
        logger.error("No database connection available.")
        return 'Database connection error', 500
    
def merge_values(base, new):
    if not base:
        base_list = []
    else:
        base_list = [b.strip() for b in base.split(';') if b.strip()]
    new_list = [n.strip() for n in new.split(';') if n.strip()]
    merged = base_list + new_list
    # Remove duplicates while preserving order
    seen = set()
    merged_unique = []
    for item in merged:
        if item not in seen:
            seen.add(item)
            merged_unique.append(item)
    return ';'.join(merged_unique) if merged_unique else ''

@app.route('/edit_word/<int:word_id>', methods=['POST'])
def update_word(word_id):
    if not session.get('logged_in'):
        return redirect(url_for('login'))

    conn = get_db_connection(pool)
    if conn:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        try:
            cur.execute('SELECT * FROM gondi_wordnet WHERE id = %s;', (word_id,))
            current_word = cur.fetchone()
            if not current_word:
                return 'Word not found', 404

            # Retrieve form data
            words_english_new = request.form.get('words_english', '').strip()
            pos_new = request.form.get('pos', current_word['pos']).strip('.').lower()
            words_hindi = request.form.get('words_hindi', current_word['words_hindi']).strip()
            definition = request.form.get('definition', current_word['definition']).strip()
            hypernym = request.form.get('hypernym', current_word['hypernym']).strip()
            hyponym = request.form.get('hyponym', current_word['hyponym']).strip()
            action = request.form.get('action')

            # Split English words into a list
            english_words = [word.strip() for word in words_english_new.split(';') if word.strip()]
            if not english_words:
                flash('No English words provided.', 'danger')
                return redirect(url_for('review_entries'))

            # Delete the original entry
            cur.execute('DELETE FROM gondi_wordnet WHERE id = %s;', (word_id,))

            # Insert new entries for each English word
            for english_word in english_words:
                # Generate synset data for the individual word
                wordnet_data = generate_synset_data(english_word, pos_new)
                name = wordnet_data['name']
                pos = wordnet_data['pos']
                
                # Merge hypernyms and hyponyms
                merged_hypernym = merge_values(current_word['hypernym'], hypernym)
                merged_hyponym = merge_values(current_word['hyponym'], hyponym)
                
                # Generate words_gondi
                words_gondi = devanagari_to_gondi(words_hindi, gondi_mapping)

                # Insert new synset entry
                cur.execute("""
                    INSERT INTO gondi_wordnet (name, words_english, words_gondi, words_hindi, 
                                               hin_word, definition, hypernym, hyponym, pos, status)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s);
                """, (
                    name,
                    words_english_new,  # Full list of words
                    words_gondi,
                    words_hindi,
                    current_word['hin_word'],
                    definition,
                    merged_hypernym,
                    merged_hyponym,
                    pos,
                    'approved' if action == 'Update and Approve' else 'reviewed'
                ))

            conn.commit()
            flash_message = 'Word updated and approved! Success' if action == 'Update and Approve' else 'Word updated successfully!'
            flash(flash_message, 'success')

        except Exception as e:
            conn.rollback()
            logger.exception("Unexpected error occurred.")
            flash('Error updating word.', 'danger')

        finally:
            cur.close()
            pool.putconn(conn)

    return redirect(url_for('review_entries'))

# Define a class for synsets
class SimpleSynset:
    def __init__(self, name, words_gondi, hindi_pronunciation, hin_word, words_english, definition, hypernym=None, hyponym=None, pos=None):
        self.name = name
        self.words_gondi = words_gondi
        self.hindi_pronunciation = hindi_pronunciation
        self.hin_word = hin_word
        self.words_english = words_english
        self.definition = definition
        self.hypernym = hypernym
        self.hyponym = hyponym
        self.pos = pos  # Part of speech (noun, verb, etc.)


def load_gondi_wordnet_from_db(status=None):
    synsets = {}
    conn = get_db_connection(pool)
    if conn:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        try:
            if status:
                cur.execute('SELECT * FROM gondi_wordnet WHERE status = %s;', (status,))
            else:
                cur.execute('SELECT * FROM gondi_wordnet;')
            rows = cur.fetchall()
            logger.debug("Fetched rows from database: %s", rows)
            
            for row in rows:
                synset = SimpleSynset(
                    name=row['name'],
                    words_gondi=row['words_gondi'],
                    hindi_pronunciation=row['words_hindi'],
                    hin_word=row['hin_word'],
                    words_english=row['words_english'],
                    definition=row['definition'],
                    hypernym=row['hypernym'],
                    hyponym=row['hyponym'],
                    pos=row['pos']
                )
                synsets[synset.name] = synset
        finally:
            cur.close()
            pool.putconn(conn)
    return synsets
    
def visualize_gondi_wordnet_pyvis(synsets):
    G = nx.DiGraph()

    # Add nodes and edges
    for synset in synsets.values():
        node_label = f"{synset.words_gondi}\n{synset.hin_word}\n{synset.words_english}\n"

        # Tooltip with full details
        node_details = (
            f"Gondi: {synset.words_gondi}\n"
            f"Hindi Pronunciation: {synset.hindi_pronunciation}\n"
            f"Hindi Word: {synset.hin_word}\n"
            f"English: {synset.words_english}\n"
            f"POS: {synset.pos}\n"
            f"Hypernym: {synset.hypernym}\n"
            f"Hyponym: {synset.hyponym}"
        )

        G.add_node(synset.name, label=node_label, title=node_details)  # Tooltip on hover

        # Add hypernyms (split by semicolon)
        if synset.hypernym:
            hypernyms = [h.strip() for h in synset.hypernym.split(';')]
            for hypernym in hypernyms:
                if hypernym and hypernym in synsets:
                    G.add_edge(hypernym, synset.name, label="hypernym")

        # Add hyponyms (split by semicolon)
        if synset.hyponym:
            hyponyms = [h.strip() for h in synset.hyponym.split(';')]
            for hyponym in hyponyms:
                if hyponym and hyponym in synsets:
                    G.add_edge(synset.name, hyponym, label="hyponym")


    # Create a PyVis network object with a pastel blue theme
    net = Network(
        notebook=True,
        directed=True,
        height="100%",
        width="100%",
        bgcolor="#E8F1F8",  # Light pastel blue background
        font_color="#3D4F5C"  # Deep steel gray text
    )

    # Convert the NetworkX graph to PyVis
    net.from_nx(G)

    # Apply pastel blue theme to nodes
    for node in net.nodes:
        node["color"] = "#A7C7E7"  # Soft pastel blue
        node["borderWidth"] = 2
        node["borderColor"] = "#6A9AC4"  # Slightly darker pastel blue
        node["font"] = {"color": "#3D4F5C", "size": 22}  # Deep steel gray

    # Set visualization options with smooth aesthetics
    net.set_options("""
{
  "nodes": {
    "shape": "dot",
    "size": 45,
    "color": {
      "background": "#A7C7E7",
      "border": "#6A9AC4",
      "highlight": {
        "background": "#91BCE0",
        "border": "#6A9AC4"
      },
      "hover": {
        "background": "#91BCE0",
        "border": "#6A9AC4"
      }
    },
    "font": {
      "color": "#3D4F5C",
      "strokeWidth": 0
    }
  },
  "edges": {
    "color": {
      "color": "#7D9EB5",
      "highlight": "#6A9AC4",
      "hover": "#6A9AC4"
    },
    "width": 1,
    "smooth": true,
    "arrows": {
      "to": {
        "enabled": true,
        "scaleFactor": 1.5
      }
    }
  },
  "physics": {
    "enabled": true,
    "barnesHut": {
      "gravitationalConstant": -4500,
      "springLength": 140,
      "springConstant": 0.08,
      "damping": 0.9,
      "avoidOverlap": 0.5
    },
    "minVelocity": 0.1,
    "maxVelocity": 10,
    "stabalization": {
        "enabled": true,
        "iterations": 1000,
        "updateInterval": 10
    },
    "solver": "barnesHut"
  },
  "interaction": {
    "zoomView": true,
    "dragNodes": true,
    "dragView": true,
    "tooltipDelay": 50,
    "navigationButtons": true,
    "zoom speed": 5.0,
    "keyboard": false
  },
  "layout": {
    "randomSeed": 42,
    "improvedLayout": true
  }
}
""")
    # Generate the HTML content as a string
    html_content = net.generate_html()
# Inject the custom font into the HTML content
    custom_font_html = """
    <link href="https://fonts.googleapis.com/css2?family=Noto+Sans+Masaram+Gondi&display=swap" rel="stylesheet">
    <style>
        body, .vis-network, .vis-node {
            font-family: "Noto Sans Masaram Gondi", sans-serif !important;
        }
        .vis-label {
            font-family: "Noto Sans Masaram Gondi", sans-serif !important;
        }
    </style>
    """

    # Insert the custom font link into the HTML head
    html_content = html_content.replace("<head>", f"<head>{custom_font_html}")

    return html_content

@app.route('/wordnet_visualization')
def wordnet_visualization():
    # Load only approved synsets
    gondi_synsets = load_gondi_wordnet_from_db(status='approved')
    
    # Generate the PyVis visualization HTML
    pyvis_html = visualize_gondi_wordnet_pyvis(gondi_synsets)
    
    
    # Pass the modified HTML content to the template
    return render_template('wordnet_visualization.html', pyvis_html=pyvis_html)

@app.route('/edit_word_review/<int:word_id>', methods=['POST'])
def update_word_review(word_id):
    if not session.get('logged_in'):
        return redirect(url_for('login_review'))

    conn = get_db_connection(pool)
    if conn:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        try:
            cur.execute('SELECT * FROM gondi_wordnet WHERE id = %s;', (word_id,))
            current_word = cur.fetchone()
            if not current_word:
                return 'Word not found', 404

            # Retrieve form data
            words_english_new = request.form.get('words_english', '').strip()
            pos_new = request.form.get('pos', current_word['pos']).strip('.').lower()
            words_hindi = request.form.get('words_hindi', current_word['words_hindi']).strip()
            definition = request.form.get('definition', current_word['definition']).strip()
            hypernym = request.form.get('hypernym', current_word['hypernym']).strip()
            hyponym = request.form.get('hyponym', current_word['hyponym']).strip()
            action = request.form.get('action')

            # Split English words into a list
            english_words = [word.strip() for word in words_english_new.split(';') if word.strip()]
            if not english_words:
                flash('No English words provided.', 'danger')
                return redirect(url_for('review_entries'))

            # Delete the original entry
            cur.execute('DELETE FROM gondi_wordnet WHERE id = %s;', (word_id,))

            # Insert new entries for each English word
            for english_word in english_words:
                # Generate synset data for the individual word
                wordnet_data = generate_synset_data(english_word, pos_new)
                name = wordnet_data['name']
                pos = wordnet_data['pos']
                
                # Merge hypernyms and hyponyms
                merged_hypernym = merge_values(current_word['hypernym'], hypernym)
                merged_hyponym = merge_values(current_word['hyponym'], hyponym)
                
                # Generate words_gondi
                words_gondi = devanagari_to_gondi(words_hindi, gondi_mapping)

                # Insert new synset entry
                cur.execute("""
                    INSERT INTO gondi_wordnet (name, words_english, words_gondi, words_hindi, 
                                               hin_word, definition, hypernym, hyponym, pos, status)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s);
                """, (
                    name,
                    words_english_new,  # Full list of words
                    words_gondi,
                    words_hindi,
                    current_word['hin_word'],
                    definition,
                    merged_hypernym,
                    merged_hyponym,
                    pos,
                    'reviewed'
                ))

            conn.commit()
            flash_message ='Word updated successfully!'
            flash(flash_message, 'success')

        except Exception as e:
            conn.rollback()
            logger.exception("Unexpected error occurred.")
            flash('Error updating word.', 'danger')

        finally:
            cur.close()
            pool.putconn(conn)

    return redirect(url_for('review'))

def get_row_count(status_list=['approved']):
    conn = get_db_connection(pool)
    if conn:
        cur = conn.cursor()
        try:
            cur.execute('SELECT COUNT(*) FROM gondi_wordnet WHERE status = ANY(%s);', (status_list,))
            row_count = cur.fetchone()[0]
            logger.debug("Fetched row count for status_list '%s': %d", status_list, row_count)
            return row_count
        except (Exception, psycopg2.Error) as error:
            logger.error("Error while fetching row count from PostgreSQL: %s", error)
            return 0
        finally:
            cur.close()
            pool.putconn(conn)
    else:
        return 0

if __name__ == '__main__':
    app.run(debug=False, host='0.0.0.0')  # For production, use a WSGI server
