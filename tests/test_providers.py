from bot.providers import Course, course_slug, filter_courses
from bot.telegram_bot import matches_filter


class TestCourseSlug:
    def test_slug_from_url(self):
        assert course_slug("https://www.udemy.com/course/python-101/") == "python-101"

    def test_slug_with_query(self):
        assert course_slug("https://www.udemy.com/course/python-101/?coupon=ABC") == "python-101"

    def test_slug_no_trailing_slash(self):
        assert course_slug("https://www.udemy.com/course/python-101") == "python-101"


class TestFilterCourses:
    def test_no_query_returns_limited(self):
        courses = [
            Course(id="1", title="Python Basics", url="https://example.com/1", source="test"),
            Course(id="2", title="Advanced Rust", url="https://example.com/2", source="test"),
        ]
        result = filter_courses(courses, "", limit=1)
        assert len(result) == 1
        assert result[0].title == "Python Basics"

    def test_query_matches_title(self):
        courses = [
            Course(id="1", title="Python Basics", url="https://example.com/1", source="test"),
            Course(id="2", title="Advanced Rust", url="https://example.com/2", source="test"),
        ]
        result = filter_courses(courses, "python", limit=10)
        assert len(result) == 1
        assert result[0].id == "1"

    def test_query_matches_multiple_terms(self):
        courses = [
            Course(id="1", title="Python for Data Science", url="https://example.com/1", source="test"),
            Course(id="2", title="Python Basics", url="https://example.com/2", source="test"),
            Course(id="3", title="Machine Learning", url="https://example.com/3", source="test"),
        ]
        result = filter_courses(courses, "python data", limit=10)
        assert len(result) == 1
        assert result[0].id == "1"

    def test_no_match_returns_empty(self):
        courses = [
            Course(id="1", title="Python Basics", url="https://example.com/1", source="test"),
        ]
        result = filter_courses(courses, "rust", limit=10)
        assert len(result) == 0

    def test_respects_limit(self):
        courses = [
            Course(id=str(i), title=f"Course {i}", url=f"https://example.com/{i}", source="test")
            for i in range(20)
        ]
        result = filter_courses(courses, "", limit=5)
        assert len(result) == 5

    def test_title_match_outranks_description(self):
        courses = [
            Course(id="1", title="Python Course", url="https://example.com/1", source="test"),
            Course(id="2", title="Rust Course mentioning Python", url="https://example.com/2", source="test"),
        ]
        result = filter_courses(courses, "python", limit=10)
        assert result[0].id == "1"


class TestCourse:
    def test_provider_label(self):
        c = Course(id="1", title="Test", url="https://example.com", source="tutorialbar")
        assert c.provider == "Udemy"

    def test_provider_fallback(self):
        c = Course(id="1", title="Test", url="https://example.com", source="unknown")
        assert c.provider == "unknown"

    def test_search_blob(self):
        c = Course(
            id="1", title="Python Basics", url="https://example.com", source="test",
            instructor="John", level="Beginner", tags=["programming"],
        )
        blob = c.search_blob()
        assert "python" in blob
        assert "beginner" in blob
        assert "programming" in blob
        assert "john" in blob

    def test_search_blob_is_lowercase(self):
        c = Course(id="1", title="PYTHON", url="https://example.com", source="test")
        blob = c.search_blob()
        assert "python" in blob.split()


class TestMatchesFilter:
    def test_no_keywords_gets_everything(self):
        c = Course(id="1", title="Python Basics", url="https://example.com", source="test")
        assert matches_filter(c, "") is True
        assert matches_filter(c, None) is True

    def test_keyword_match(self):
        c = Course(id="1", title="Python Basics", url="https://example.com", source="test")
        assert matches_filter(c, "python") is True

    def test_no_keyword_match(self):
        c = Course(id="1", title="Rust Basics", url="https://example.com", source="test")
        assert matches_filter(c, "python") is False

    def test_or_match_multiple_keywords(self):
        c = Course(id="1", title="Python Basics", url="https://example.com", source="test")
        assert matches_filter(c, "python,rust") is True

    def test_case_insensitive(self):
        c = Course(id="1", title="PYTHON Basics", url="https://example.com", source="test")
        assert matches_filter(c, "python") is True


class TestPlatformFiltering:
    """Test the platform filtering logic in CourseBot."""

    def test_resolve_platform_aliases(self):

        from bot.config import Config
        from bot.telegram_bot import CourseBot
        cfg = Config(
            telegram_token="test",
            udemy_client_id=None, udemy_client_secret=None,
            coupon_feeds=[], tutorialbar=True,
            tutorialbar_categories=[],
            search_coursera=True, search_edx=True,
            search_mit=True, search_freecodecamp=True,
            search_catalogue_pages=5, search_timeout=25,
            db_path=":memory:", poll_interval=3600,
            max_per_cycle=10, language="en", admin_chat_id=None,
        )
        bot = CourseBot(cfg)
        assert bot._resolve_platform("coursera") == "coursera"
        assert bot._resolve_platform("course") == "coursera"
        assert bot._resolve_platform("edx") == "edx"
        assert bot._resolve_platform("mit") == "mit"
        assert bot._resolve_platform("freecodecamp") == "freecodecamp"
        assert bot._resolve_platform("coursera") == "coursera"

    def test_filter_by_platform(self):
        import os
        import tempfile

        from bot.config import Config
        from bot.providers import Course
        from bot.telegram_bot import CourseBot
        c1 = Course(id="1", title="Python", url="https://example.com/1", source="coursera")
        c2 = Course(id="2", title="Rust", url="https://example.com/2", source="edx")
        c3 = Course(id="3", title="Data", url="https://example.com/3", source="coursera")
        with tempfile.TemporaryDirectory() as td:
            cfg = Config(
                telegram_token="test",
                udemy_client_id=None, udemy_client_secret=None,
                coupon_feeds=[], tutorialbar=True,
                tutorialbar_categories=[],
                search_coursera=True, search_edx=True,
                search_mit=True, search_freecodecamp=True,
                search_catalogue_pages=5, search_timeout=25,
                db_path=os.path.join(td, "test.db"), poll_interval=3600,
                max_per_cycle=10, language="en", admin_chat_id=None,
            )
            bot = CourseBot(cfg)
            courses = [c1, c2, c3]
            filtered = bot._filter_by_platform(courses, "coursera")
            assert len(filtered) == 2
            assert all(c.source == "coursera" for c in filtered)

    def test_filter_by_platform_no_match(self):
        import os
        import tempfile

        from bot.config import Config
        from bot.providers import Course
        from bot.telegram_bot import CourseBot
        c1 = Course(id="1", title="Python", url="https://example.com/1", source="coursera")
        c2 = Course(id="2", title="Rust", url="https://example.com/2", source="edx")
        with tempfile.TemporaryDirectory() as td:
            cfg = Config(
                telegram_token="test",
                udemy_client_id=None, udemy_client_secret=None,
                coupon_feeds=[], tutorialbar=True,
                tutorialbar_categories=[],
                search_coursera=True, search_edx=True,
                search_mit=True, search_freecodecamp=True,
                search_catalogue_pages=5, search_timeout=25,
                db_path=os.path.join(td, "test.db"), poll_interval=3600,
                max_per_cycle=10, language="en", admin_chat_id=None,
            )
            bot = CourseBot(cfg)
            courses = [c1, c2]
            filtered = bot._filter_by_platform(courses, "unknown")
            assert len(filtered) == 0
