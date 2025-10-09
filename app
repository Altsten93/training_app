<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Google Sheet Planner</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
    <style>
        body {
            font-family: 'Inter', sans-serif;
        }
        /* Simple spinner animation */
        .loader {
            border: 4px solid #f3f3f3;
            border-top: 4px solid #3498db;
            border-radius: 50%;
            width: 40px;
            height: 40px;
            animation: spin 1s linear infinite;
        }
        @keyframes spin {
            0% { transform: rotate(0deg); }
            100% { transform: rotate(360deg); }
        }
    </style>
</head>
<body class="bg-gray-900 text-white flex items-center justify-center min-h-screen p-4">

    <div class="bg-gray-800 w-full max-w-md p-6 md:p-8 rounded-2xl shadow-2xl border border-gray-700">
        
        <header class="text-center mb-6">
            <h1 class="text-3xl font-bold text-white">Meal Planner</h1>
            <p class="text-gray-400 mt-2">Paste your Google Sheet URL to see your meals.</p>
        </header>

        <!-- Input Section -->
        <div class="space-y-4">
            <div>
                <label for="sheet-url" class="block text-sm font-medium text-gray-300 mb-2">Published Google Sheet URL</label>
                <input type="url" id="sheet-url" placeholder="https://docs.google.com/..." class="w-full bg-gray-700 border border-gray-600 text-white rounded-lg px-4 py-2 focus:ring-2 focus:ring-blue-500 focus:outline-none transition">
            </div>
            <button id="fetch-button" class="w-full bg-blue-600 hover:bg-blue-700 text-white font-bold py-3 px-4 rounded-lg transition-transform transform hover:scale-105 shadow-lg">
                Get My Meal Plan
            </button>
        </div>

        <!-- Output Section -->
        <div id="output-container" class="mt-8">
            <div id="loader" class="hidden mx-auto loader"></div>
            <div id="error-message" class="hidden text-red-400 bg-red-900/50 p-3 rounded-lg text-center"></div>
            <div id="results" class="hidden space-y-4">
                <!-- Conclusions will be rendered here -->
            </div>
        </div>

    </div>

    <script>
        // DOM Elements
        const sheetUrlInput = document.getElementById('sheet-url');
        const fetchButton = document.getElementById('fetch-button');
        const loader = document.getElementById('loader');
        const errorMessage = document.getElementById('error-message');
        const resultsContainer = document.getElementById('results');

        // Event listener for the button
        fetchButton.addEventListener('click', handleFetchData);

        /**
         * Main function to handle fetching and processing the data.
         */
        async function handleFetchData() {
            const url = sheetUrlInput.value.trim();
            if (!url) {
                showError("Please paste your Google Sheet URL.");
                return;
            }
            resetUI();
            loader.classList.remove('hidden');
            fetchButton.disabled = true;
            fetchButton.textContent = 'Fetching...';

            try {
                const response = await fetch(url);
                if (!response.ok) throw new Error(`Network error: Could not fetch the sheet. Status: ${response.status}`);
                const csvText = await response.text();
                const data = parseCSV(csvText);
                if (data.length === 0) throw new Error("The sheet appears to be empty or in the wrong format.");
                displayConclusions(data);
            } catch (error) {
                console.error("Fetch Error:", error);
                showError(error.message || "An unknown error occurred.");
            } finally {
                loader.classList.add('hidden');
                fetchButton.disabled = false;
                fetchButton.textContent = 'Get My Meal Plan';
            }
        }
        
        /**
         * Parses a CSV text string into an array of objects.
         * Assumes the first row is the header.
         */
        function parseCSV(text) {
            const lines = text.trim().split('\n');
            if (lines.length < 2) return [];
            const headers = lines[0].split(',').map(h => h.trim());
            const rows = [];
            for (let i = 1; i < lines.length; i++) {
                const values = lines[i].split(',').map(v => v.trim());
                if (values.length === headers.length) {
                    let rowObject = {};
                    headers.forEach((header, index) => {
                        rowObject[header] = values[index];
                    });
                    rows.push(rowObject);
                }
            }
            return rows;
        }

        /**
         * Analyzes the meal data and renders conclusions to the DOM.
         */
        function displayConclusions(data) {
            resultsContainer.innerHTML = ''; // Clear previous results
            
            // --- CONCLUSION 1: What's for dinner tonight? ---
            const todayName = new Date().toLocaleDateString('en-US', { weekday: 'long' });
            const todaysDinner = data.find(meal => meal.Day?.toLowerCase() === todayName.toLowerCase() && meal.Category?.toLowerCase() === 'dinner');
            
            const dinnerTitle = document.createElement('h2');
            dinnerTitle.className = 'text-xl font-bold text-center text-blue-300';
            dinnerTitle.textContent = "Tonight's Dinner";
            resultsContainer.appendChild(dinnerTitle);

            if (todaysDinner) {
                const card = document.createElement('div');
                card.className = 'bg-gray-700 p-4 rounded-lg shadow-md border border-gray-600';
                card.innerHTML = `
                    <p class="text-lg font-semibold">${todaysDinner.Meal}</p>
                    <p class="text-sm text-gray-400 mt-1">Ingredients: <span class="text-gray-200">${todaysDinner.Ingredients}</span></p>
                    <p class="text-sm text-gray-400">Cook Time: <span class="font-medium text-orange-400">${todaysDinner['Cook Time (mins)']} mins</span></p>
                `;
                resultsContainer.appendChild(card);
            } else {
                 const noDinner = document.createElement('p');
                 noDinner.className = 'text-center text-gray-400 bg-gray-700/50 p-3 rounded-lg';
                 noDinner.textContent = `No dinner planned for ${todayName}.`;
                 resultsContainer.appendChild(noDinner);
            }

            // --- CONCLUSION 2: Quickest Meal ---
            const quickestMeal = data.reduce((quickest, current) => {
                const quickTime = parseInt(quickest['Cook Time (mins)'], 10);
                const currentTime = parseInt(current['Cook Time (mins)'], 10);
                return (currentTime < quickTime) ? current : quickest;
            }, data[0]);

            const quickTitle = document.createElement('h2');
            quickTitle.className = 'text-xl font-bold text-center text-blue-300 mt-6';
            quickTitle.textContent = "Quickest Meal";
            resultsContainer.appendChild(quickTitle);
            
            if(quickestMeal) {
                const card = document.createElement('div');
                card.className = 'bg-gray-700 p-4 rounded-lg shadow-md border border-gray-600';
                card.innerHTML = `
                    <p class="text-lg font-semibold">${quickestMeal.Meal}</p>
                    <p class="text-sm text-gray-400">Category: <span class="text-gray-200">${quickestMeal.Category}</span></p>
                    <p class="text-sm text-gray-400">Cook Time: <span class="font-medium text-orange-400">${quickestMeal['Cook Time (mins)']} mins</span></p>
                `;
                resultsContainer.appendChild(card);
            }

            // --- DISPLAYING THE FULL MEAL PLAN ---
            const allTasksTitle = document.createElement('h3');
            allTasksTitle.className = 'text-lg font-semibold mt-8 mb-2 text-center text-gray-300';
            allTasksTitle.textContent = 'Full Meal Plan';
            resultsContainer.appendChild(allTasksTitle);

            const list = document.createElement('ul');
            list.className = 'space-y-2';
            data.forEach(item => {
                const listItem = document.createElement('li');
                listItem.className = 'bg-gray-700/50 p-3 rounded-md flex justify-between items-center';
                listItem.innerHTML = `
                    <div>
                        <p class="font-medium">${item.Meal || 'N/A'}</p>
                        <p class="text-xs text-gray-400">${item.Day || 'N/A'} - ${item['Cook Time (mins)'] || '?'} mins</p>
                    </div>
                    <span class="text-xs font-semibold py-1 px-2 rounded-full ${getCategoryClass(item.Category)}">${item.Category || 'N/A'}</span>
                `;
                list.appendChild(listItem);
            });
            resultsContainer.appendChild(list);

            resultsContainer.classList.remove('hidden');
        }
        
        function getCategoryClass(category) {
            if (!category) return 'bg-gray-500 text-white';
            switch (category.toLowerCase()) {
                case 'dinner': return 'bg-indigo-500 text-white';
                case 'lunch': return 'bg-green-500 text-white';
                case 'breakfast': return 'bg-yellow-500 text-black';
                default: return 'bg-gray-500 text-white';
            }
        }

        function showError(message) {
            errorMessage.textContent = `Error: ${message}. Make sure your sheet is published as a .csv file and the URL is correct.`;
            errorMessage.classList.remove('hidden');
        }

        function resetUI() {
            errorMessage.classList.add('hidden');
            resultsContainer.classList.add('hidden');
            resultsContainer.innerHTML = '';
        }
    </script>
</body>
</html>

