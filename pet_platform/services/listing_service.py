"""Listings: public search, availability parsing and trainer edits."""
import os
import re

from flask import current_app

from pet_platform.db import get_db
from pet_platform.utils.uploads import save_upload

CATEGORIES = ('dog', 'cat')
WEEKDAYS = {'週日': 0, '週一': 1, '週二': 2, '週三': 3, '週四': 4, '週五': 5, '週六': 6}


def search_listings(query='', category='', viewer_id=None):
    """Approved listings, newest first, filtered by text / category; hides trainers who blocked the viewer."""
    sql = "SELECT * FROM listings WHERE status = 'approved'"
    params = []
    if category in CATEGORIES:
        sql += ' AND pet_type = ?'
        params.append(category)
    if query:
        pattern = f'%{query}%'
        sql += ' AND (pet_name LIKE ? OR breed LIKE ? OR pet_type LIKE ? OR introduction LIKE ?)'
        params += [pattern] * 4
    conn = get_db()
    listings = conn.execute(sql + ' ORDER BY created_at DESC', params).fetchall()

    if viewer_id is not None:
        blocked = {row['trainer_id'] for row in
                   conn.execute('SELECT trainer_id FROM blacklists WHERE consumer_id = ?', (viewer_id,)).fetchall()}
        listings = [l for l in listings if l['trainer_id'] not in blocked]
    return listings


def top_rated_listings(category=''):
    """Up to 6 approved listings with the best average buyer rating."""
    where = "WHERE l.status = 'approved'"
    params = []
    if category in CATEGORIES:
        where += ' AND l.pet_type = ?'
        params.append(category)
    return get_db().execute(f'''
        SELECT l.*,
               COALESCE(AVG(r.rating), 0) as avg_rating,
               COUNT(r.id) as review_count,
               u.name as trainer_name, u.nickname as trainer_nickname
        FROM listings l
        LEFT JOIN orders o ON l.id = o.listing_id
        LEFT JOIN reviews r ON o.id = r.order_id AND r.review_type = 'buyer_to_trainer'
        JOIN users u ON l.trainer_id = u.id
        {where}
        GROUP BY l.id
        HAVING avg_rating > 0
        ORDER BY avg_rating DESC, review_count DESC
        LIMIT 6
    ''', params).fetchall()


def booking_window(available_slots):
    """From "週一, 週三 09:00 - 18:00" return (min_hour, max_hour, weekdays) with 0=Sun ... 6=Sat."""
    min_hour, max_hour, weekdays = 9, 22, []
    if available_slots:
        match = re.search(r'(\d{2}):(\d{2})\s*-\s*(\d{2}):(\d{2})', available_slots)
        if match:
            min_hour, max_hour = int(match.group(1)), int(match.group(3))
        weekdays = [value for name, value in WEEKDAYS.items() if name in available_slots]
    return min_hour, max_hour, weekdays


def slot_form_data(available_slots):
    """Pre-fill values for the edit form from "週一, 週二 09:00 - 18:00"."""
    data = {'days': [], 'start_time': '09:00', 'end_time': '18:00'}
    if available_slots:
        match = re.match(r'^(.*) (\d{2}:\d{2}) - (\d{2}:\d{2})$', available_slots)
        if match:
            data['days'] = [day.strip() for day in match.group(1).split(',')]
            data['start_time'] = match.group(2)
            data['end_time'] = match.group(3)
    return data


def format_slots(days, start_time, end_time):
    return f"{', '.join(days)} {start_time} - {end_time}"


def listing_images(listing_id):
    rows = get_db().execute('SELECT filename FROM listing_images WHERE listing_id = ? ORDER BY id ASC',
                            (listing_id,)).fetchall()
    return [r['filename'] for r in rows]


def update_listing(listing, trainer_id, fields, new_files, delete_filenames):
    """Save a trainer's edit; the listing goes back to review as modification_pending."""
    conn = get_db()
    listing_id = listing['id']

    for file in new_files:
        if file.filename:
            filename = save_upload(file, f"listing_{trainer_id}_{listing['pet_type']}")
            conn.execute('INSERT INTO listing_images (listing_id, filename) VALUES (?, ?)', (listing_id, filename))

    for filename in delete_filenames:
        conn.execute('DELETE FROM listing_images WHERE listing_id = ? AND filename = ?', (listing_id, filename))
        # Best effort: the DB row is what matters, a missing file is fine
        try:
            os.remove(os.path.join(current_app.config['UPLOAD_FOLDER'], filename))
        except Exception:
            pass

    conn.execute('''
        UPDATE listings
        SET price = ?, cancellation_hours = ?, available_slots = ?, introduction = ?, rules = ?, status = 'modification_pending'
        WHERE id = ?
    ''', (fields['price'], fields['cancellation_hours'], fields['available_slots'],
          fields['introduction'], fields['rules'], listing_id))

    # The first remaining image becomes the cover
    first_image = conn.execute('SELECT filename FROM listing_images WHERE listing_id = ? ORDER BY id ASC LIMIT 1',
                               (listing_id,)).fetchone()
    if first_image:
        conn.execute('UPDATE listings SET pet_image = ? WHERE id = ?', (first_image['filename'], listing_id))
    conn.commit()
