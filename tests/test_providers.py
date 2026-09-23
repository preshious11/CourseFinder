from providers import Course, course_slug, filter_courses


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
