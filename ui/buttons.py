CB_MAIN_FIND_SPECIALIST = "M_FIND"

CB_SEARCH_START = "search_start"
CB_SEARCH_MENU = "search_menu"

CB_SEARCH_CATEGORY = "search_category"
CB_SEARCH_CATEGORY_PAGE = "search_categories_page"

CB_SEARCH_PROFESSION = "search_profession"
CB_SEARCH_PROFESSION_PAGE = "search_professions_page"
CB_SEARCH_PROFESSION_ALL = "search_profession_all"

CB_SEARCH_MODE_CITY = "search_mode_city"
CB_SEARCH_MODE_GEO = "search_mode_geo"

CB_SEARCH_CITY = "search_city"
CB_SEARCH_CITY_PAGE = "search_cities_page"

CB_SEARCH_RESULT = "search_result"
CB_SEARCH_RESULTS_PAGE = "search_results_page"

CB_SEARCH_RADIUS = "search_radius"
CB_SEARCH_LANGUAGE = "search_lang"
CB_SEARCH_PRICE = "search_price"
CB_SEARCH_RATING = "search_rating"
CB_SEARCH_WORK = "search_work"

CB_SEARCH_VERIFIED_TOGGLE = "search_verified_toggle"
CB_SEARCH_PREMIUM_TOGGLE = "search_premium_toggle"
CB_SEARCH_SHOW_RESULTS = "search_show_results"

CB_SEARCH_CONTACT_PENDING = "search_contact_pending"
CB_SEARCH_FAVORITE_PENDING = "search_favorite_pending"
CB_SEARCH_REPORT_PENDING = "search_report_pending"


def cb(prefix: str, value: int | str) -> str:
    return f"{prefix}:{value}"