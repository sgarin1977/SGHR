translations = {
    "im_employer": {
        "ru": "Я работодатель",
        "pt": "Sou empregador",
        "en": "I'm an employer"
    },
    "im_seeker": {
        "ru": "Я соискатель",
        "pt": "Sou candidato",
        "en": "I'm a job seeker"
    },
    "post_vacancy": {
        "ru": "Разместить вакансию",
        "pt": "Publicar vaga",
        "en": "Post a job"
    },
    "my_vacancies": {
        "ru": "Мои вакансии",
        "pt": "Minhas vagas",
        "en": "My vacancies"
    },
    "find_job": {
        "ru": "Найти работу",
        "pt": "Encontrar trabalho",
        "en": "Find a job"
    },
    "find_specialists": {
        "ru": "Специалисты",
        "pt": "Especialistas",
        "en": "Specialists"
    },
    "profile": {
        "ru": "Профиль",
        "pt": "Perfil",
        "en": "Profile"
    },
    "edit_vacancy": {
        "ru": "✏️ Редактировать",
        "pt": "✏️ Editar",
        "en": "✏️ Edit"
    },
    "delete_vacancy": {
        "ru": "🗑️ Удалить",
        "pt": "🗑️ Excluir",
        "en": "🗑️ Delete"
    },
    "boost_vacancy": {
        "ru": "🚀 Поднять",
        "pt": "🚀 Destacar",
        "en": "🚀 Boost"
    },
    "extend_vacancy": {
        "ru": "🔁 Продлить",
        "pt": "🔁 Prorrogar",
        "en": "🔁 Extend"
    },
    "view_stats": {
        "ru": "📊 Статистика",
        "pt": "📊 Estatísticas",
        "en": "📊 Stats"
    },
    "view_resume": {
        "ru": "📄 Посмотреть резюме",
        "pt": "📄 Ver currículo",
        "en": "📄 View resume"
    },
    "edit_resume": {
        "ru": "✏️ Редактировать резюме",
        "pt": "✏️ Editar currículo",
        "en": "✏️ Edit resume"
    },
    "send_resume": {
        "ru": "📤 Разослать резюме",
        "pt": "📤 Enviar currículo",
        "en": "📤 Send resume"
    },
    "more_info": {
        "ru": "📄 Подробнее",
        "pt": "📄 Detalhes",
        "en": "📄 More info"
    },
    "respond": {
        "ru": "📬 Откликнуться",
        "pt": "📬 Candidatar-se",
        "en": "📬 Apply"
    },
    "favorite": {
        "ru": "⭐ В избранное",
        "pt": "⭐ Favoritar",
        "en": "⭐ Favorite"
    },
    "report": {
        "ru": "⚠️ Пожаловаться",
        "pt": "⚠️ Denunciar",
        "en": "⚠️ Report"
    },
    "back": {
        "ru": "⬅️ Назад",
        "pt": "⬅️ Voltar",
        "en": "⬅️ Back"
    },
    "cancel": {
        "ru": "❌ Отмена",
        "pt": "❌ Cancelar",
        "en": "❌ Cancel"
    },
    "confirm": {
        "ru": "✅ Подтвердить",
        "pt": "✅ Confirmar",
        "en": "✅ Confirm"
    },
    "switch_profile": {
        "ru": "Переключить профиль",
        "pt": "Alternar perfil",
        "en": "Switch profile"
    },
    "help": {
        "ru": "Помощь",
        "pt": "Ajuda",
        "en": "Help"
    },
    "register_seeker": {
        "ru": "Зарегистрироваться как соискатель",
        "pt": "Registrar-se como candidato",
        "en": "Register as seeker"
    },
    "register_employer": {
        "ru": "Зарегистрироваться как работодатель",
        "pt": "Registrar-se como empregador",
        "en": "Register as employer"
    },
    "new_vacancies": {
        "ru": "Новые вакансии",
        "pt": "Novas vagas",
        "en": "New vacancies"
    },
    "messages_from_employers": {
        "ru": "Сообщения от работодателей",
        "pt": "Mensagens dos empregadores",
        "en": "Messages from employers"
    },
    "edit_profile": {
        "ru": "Редактировать профиль",
        "pt": "Editar perfil",
        "en": "Edit profile"
    },
    "candidates_responses": {
        "ru": "Отклики кандидатов",
        "pt": "Respostas dos candidatos",
        "en": "Candidates’ responses"
    },
    "add_vacancy": {
        "ru": "Добавить вакансию",
        "pt": "Adicionar vaga",
        "en": "Add vacancy"
    },
    "filter_by_profession": {
        "ru": "По профессии",
        "pt": "Por profissão",
        "en": "By profession"
    },
    "filter_by_region": {
        "ru": "По региону",
        "pt": "Por região",
        "en": "By region"
    },
    "filter_by_city": {
        "ru": "По городу",
        "pt": "Por cidade",
        "en": "By city"
    },
    "filter_by_distance": {
        "ru": "Ближайшие ко мне",
        "pt": "Mais próximos de mim",
        "en": "Nearest to me"
    },
    "find_nearby": {
    "ru": "Ближайшие ко мне",
    "pt": "Mais próximos de mim",
    "en": "Nearest to me"
    },
    
    "choose_filter": {
    "ru": "Выберите фильтр:",
    "pt": "Escolha um filtro:",
    "en": "Choose a filter:"
},



}

def t(key: str, lang: str = "ru") -> str:
    return translations.get(key, {}).get(lang, translations[key]["ru"])

